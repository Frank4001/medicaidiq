"""Hybrid semantic retrieval over policy documents (Week 3).

Pipeline (each stage traceable in the tool output):
  1. Filter by jurisdiction (+ federal docs, labelled as such) and as-of date   -> scope control
  2. BM25 keyword ranking   (exact terms: drug names, doc IDs, CFR cites)       -> D1.3
  3. Dense ranking: bge-small-en-v1.5 embeddings, cosine similarity             -> Lab 3.1 (DPR)
  4. Reciprocal Rank Fusion of 2+3 -> top 15 candidates                         -> hybrid
  5. MMR rerank (lambda 0.7) -> top k=4, avoiding near-duplicate passages        -> Lab 3.2
  6. Adjacent-paragraph expansion (next chunk of the same doc), up to 6 total   -> keeps exceptions
     and qualifications that follow a rule (from the Checkpoint 3.1 prototype)
  7. Relevance gate: if the best dense similarity < LOW_RELEVANCE the results are flagged so the
     agent must not answer from them                                             -> failure-mode guard

Chunking: paragraph-level. Synthetic docs split on blank lines. Regulations are grouped by
top-level paragraph ((a), (b), ...) with sub-paragraphs kept with their parent, and split at
~180 words. Each chunk carries its document title (DPR-style "title + text") for embedding.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .paths import DATA_DIR, POLICY_DIR, REPO_ROOT

PUBLIC_DIR = DATA_DIR / "public"
INDEX_DIR = DATA_DIR / "index"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "  # BGE recommendation
MAX_WORDS = 180
LOW_RELEVANCE = 0.68      # best passage below this -> "low_relevance" (calibrated: relevant >= 0.73, off-topic <= 0.66)
PASSAGE_FLOOR = 0.55      # individual ranked passages below this are dropped (MMR diversity noise)
FEDERAL = {"US", "ALL"}

_TOKEN = re.compile(r"[a-z0-9][a-z0-9.\-]*[a-z0-9]|[a-z0-9]")
_STOP = {"the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "is", "be", "with", "by",
         "this", "that", "are", "was", "as", "at", "from", "will", "all", "any", "what", "when", "did",
         "does", "do", "how", "which", "its", "it", "may", "must", "under", "their", "there"}


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


# ----------------------------------------------------------------------------- corpus
@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    seq: int                 # position within the document (for adjacency)
    title: str
    label: str               # paragraph label, e.g. "(b)" or "p2"
    text: str
    meta: dict = field(default_factory=dict)

    @property
    def jurisdiction(self) -> str:
        return self.meta.get("jurisdiction", "")

    def embed_text(self) -> str:
        return f"{self.title}. {self.label} {self.text}"


def _parse_md(path: Path) -> tuple[dict, str, list[str]]:
    raw = path.read_text(encoding="utf-8")
    _, front, body = raw.split("---", 2)
    meta = dict(line.split(": ", 1) if ": " in line else (line.rstrip(":"), "")
                for line in front.strip().splitlines())
    title = re.search(r"^# (.+)$", body, re.MULTILINE)
    text = re.sub(r"^>.*$|^# .+$", "", body, flags=re.MULTILINE).strip()
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return meta, title.group(1) if title else meta["doc_id"], paras


def _group_regulation(paras: list[str]) -> list[tuple[str, str]]:
    """Group sub-paragraphs under their top-level (a)/(b) parent; split long groups.

    "(i)" or "(v)" counts as a top-level letter only if it is the next expected letter
    (after (h) or (u)); otherwise it is a Roman-numeral sub-item of the current paragraph.
    """
    groups: list[tuple[str, list[str]]] = []
    expected = "a"
    for p in paras:
        m = re.match(r"^\(([a-z])\)", p)
        if m and m.group(1) == expected:
            groups.append((f"({expected})", [p]))
            expected = chr(ord(expected) + 1)
        elif not groups:
            groups.append(("preamble", [p]))
        else:
            groups[-1][1].append(p)
    out = []
    for label, ps in groups:
        buf: list[str] = []
        part = 1
        for p in ps:
            if buf and len(" ".join(buf + [p]).split()) > MAX_WORDS:
                out.append((label if part == 1 else f"{label} cont. {part}", " ".join(buf)))
                buf, part = [], part + 1
            buf.append(p)
        if buf:
            out.append((label if part == 1 else f"{label} cont. {part}", " ".join(buf)))
    return out


def load_chunks(policy_dir: Path = POLICY_DIR, public_dir: Path = PUBLIC_DIR) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(Path(policy_dir).glob("*.md")):
        meta, title, paras = _parse_md(path)
        meta = {**meta, "jurisdiction": meta.get("state", ""), "source": "synthetic test corpus",
                "url": f"data/synthetic/policies/{path.name}"}
        for i, p in enumerate(paras):
            chunks.append(Chunk(f"{meta['doc_id']}#p{i + 1}", meta["doc_id"], i, title, f"p{i + 1}", p, meta))
    for path in sorted(Path(public_dir).glob("*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        meta = {k: d[k] for k in ("doc_id", "jurisdiction", "doc_type", "source", "url", "version_date",
                                  "retrieved_at", "sha256")}
        meta.update(published=d["version_date"], effective_date="", supersedes="", state=d["jurisdiction"])
        groups = (_group_regulation(d["paragraphs"]) if d["doc_type"] == "statute_or_regulation"
                  else [(f"p{i + 1}", p) for i, p in enumerate(d["paragraphs"])])
        for i, (label, text) in enumerate(groups):
            cid = f"{d['doc_id']}{label.split()[0] if label.startswith('(') else '#' + label}"
            if label.startswith("(") and " cont." in label:
                cid += f"-{label.rsplit(' ', 1)[-1]}"
            chunks.append(Chunk(cid, d["doc_id"], i, d["title"], label, text, meta))
    ids = [c.chunk_id for c in chunks]
    dupes = {x for x in ids if ids.count(x) > 1}
    if dupes:
        raise ValueError(f"Duplicate chunk ids: {sorted(dupes)}")
    return chunks


# ----------------------------------------------------------------------------- embeddings
class Embedder:
    """bge-small-en-v1.5 via fastembed (ONNX, CPU, no API cost). Index cached by corpus hash."""

    def __init__(self, cache_dir: Path = REPO_ROOT / ".tools" / "fastembed"):
        from fastembed import TextEmbedding
        self.model = TextEmbedding(EMBED_MODEL, cache_dir=str(cache_dir))

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.array(list(self.model.embed(texts)), dtype=np.float32)

    def embed_query(self, q: str) -> np.ndarray:
        return self.embed([QUERY_INSTRUCTION + q])[0]


def build_index(chunks: list[Chunk], embedder: Embedder, index_dir: Path = INDEX_DIR) -> np.ndarray:
    corpus_hash = hashlib.sha256("\n".join(c.chunk_id + c.embed_text() for c in chunks).encode()).hexdigest()[:16]
    path = Path(index_dir) / f"embeddings_{corpus_hash}.npy"
    if path.exists():
        return np.load(path)
    vecs = embedder.embed([c.embed_text() for c in chunks])
    path.parent.mkdir(parents=True, exist_ok=True)
    for old in path.parent.glob("embeddings_*.npy"):
        old.unlink()
    np.save(path, vecs)
    return vecs


# ----------------------------------------------------------------------------- search
def mmr_select(query_vec: np.ndarray, cand_vecs: np.ndarray, k: int, lam: float = 0.7) -> list[int]:
    """Maximal Marginal Relevance (Carbonell & Goldstein 1998), as in Lab 3.2."""
    rel = cand_vecs @ query_vec
    selected: list[int] = []
    remaining = list(range(len(cand_vecs)))
    while remaining and len(selected) < k:
        def score(i):
            redundancy = max((float(cand_vecs[i] @ cand_vecs[j]) for j in selected), default=0.0)
            return lam * float(rel[i]) - (1 - lam) * redundancy
        best = max(remaining, key=score)
        selected.append(best)
        remaining.remove(best)
    return selected


class PolicySearch:
    def __init__(self, chunks: list[Chunk] | None = None, mode: str = "hybrid", embedder: Embedder | None = None,
                 k: int = 4, max_results: int = 6, mmr_lambda: float = 0.7):
        self.chunks = chunks if chunks is not None else load_chunks()
        self.k, self.max_results, self.mmr_lambda = k, max_results, mmr_lambda
        self.mode = mode
        self.vecs = None
        if mode in ("hybrid", "dense"):
            try:
                self.embedder = embedder or Embedder()
                self.vecs = build_index(self.chunks, self.embedder)
            except ImportError:      # fastembed not installed -> degrade gracefully, and say so
                self.mode = "bm25"
        self._bm25_init()
        self.superseded_by = {c.meta.get("supersedes"): c.doc_id for c in self.chunks if c.meta.get("supersedes")}
        self.by_doc: dict[str, list[int]] = {}
        for i, c in enumerate(self.chunks):
            self.by_doc.setdefault(c.doc_id, []).append(i)

    def _bm25_init(self, k1: float = 1.5, b: float = 0.75):
        self.toks = [tokenize(f"{c.title} {c.text} {c.doc_id} {c.meta.get('topic', '')} "
                              f"{c.meta.get('drug_class', '')}") for c in self.chunks]
        self.avgdl = sum(map(len, self.toks)) / max(len(self.toks), 1)
        df = Counter(t for ts in self.toks for t in set(ts))
        n = len(self.chunks)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.k1, self.b = k1, b

    def _bm25(self, q: list[str], i: int) -> float:
        tf, dl = Counter(self.toks[i]), len(self.toks[i])
        return sum(self.idf.get(t, 0) * tf[t] * (self.k1 + 1) /
                   (tf[t] + self.k1 * (1 - self.b + self.b * dl / self.avgdl)) for t in q if t in tf)

    def _eligible(self, state: str | None, as_of: str | None, include_federal: bool) -> list[int]:
        out = []
        for i, c in enumerate(self.chunks):
            j = c.jurisdiction
            if state and not (j == state.upper() or (include_federal and j in FEDERAL)):
                continue
            if not state and not include_federal and j in FEDERAL:
                continue
            if as_of and j not in FEDERAL and c.meta.get("published", "") > as_of:
                continue  # state document not yet published at the as-of date
            out.append(i)
        return out

    def search(self, query: str, state: str | None = None, as_of: str | None = None,
               include_federal: bool = True, top_k: int | None = None) -> dict:
        k = top_k or self.k
        pool = self._eligible(state, as_of, include_federal)
        qtok = tokenize(query)
        bm25 = {i: self._bm25(qtok, i) for i in pool}
        bm25_rank = {i: r for r, i in enumerate(sorted((i for i in pool if bm25[i] > 0), key=lambda i: -bm25[i]), 1)}
        dense, dense_rank, qvec = {}, {}, None
        if self.vecs is not None and pool:
            qvec = self.embedder.embed_query(query)
            sims = self.vecs[pool] @ qvec
            dense = {i: float(s) for i, s in zip(pool, sims)}
            dense_rank = {i: r for r, i in enumerate(sorted(pool, key=lambda i: -dense[i]), 1)}
        rrf = {i: (1 / (60 + bm25_rank[i]) if i in bm25_rank else 0) +
                  (1 / (60 + dense_rank[i]) if i in dense_rank else 0) for i in pool}
        candidates = [i for i in sorted(pool, key=lambda i: -rrf[i]) if rrf[i] > 0][:15]

        if qvec is not None and candidates:
            picked = [candidates[j] for j in mmr_select(qvec, self.vecs[candidates], k, self.mmr_lambda)]
        else:
            picked = candidates[:k]
        if dense and picked:   # drop weak passages that MMR pulled in for diversity (keep the best one)
            picked = [i for n, i in enumerate(picked) if n == 0 or dense[i] >= PASSAGE_FLOOR]
        results = [(i, "ranked") for i in picked]
        for i in list(picked):                     # adjacent-paragraph expansion
            if len(results) >= self.max_results:
                break
            seq = self.by_doc[self.chunks[i].doc_id]
            pos = seq.index(i)
            if pos + 1 < len(seq) and seq[pos + 1] not in [r[0] for r in results] and seq[pos + 1] in pool:
                results.append((seq[pos + 1], f"adjacent to {self.chunks[i].chunk_id}"))

        best = max((dense[i] for i in picked), default=None) if dense else None
        quality = {"mode": self.mode, "best_dense_similarity": round(best, 3) if best is not None else None,
                   "status": "ok" if (best is None and picked) or (best is not None and best >= LOW_RELEVANCE)
                   else "low_relevance"}
        if quality["status"] == "low_relevance":
            quality["warning"] = ("No passage is strongly relevant. Do not answer from these passages; "
                                  "say the corpus does not cover this.")
        covered = sorted({self.chunks[i].jurisdiction for i, _ in results})
        out = {"query": query, "filters": {"state": state, "as_of": as_of, "include_federal": include_federal},
               "retrieval_quality": quality, "jurisdictions_in_results": covered, "passages": []}
        if state and state.upper() not in covered:
            out["scope_warning"] = (f"No {state.upper()}-specific document matched. Federal passages apply to "
                                    "all states but do not establish this state's own rules.")
            # A state-specific question with zero state documents is out of scope, whatever the
            # similarity score (live case: an Illinois query scored 0.681 on federal text).
            quality["status"] = "out_of_scope"
            quality["warning"] = (f"The corpus has no documents for {state.upper()}. Do not answer state-specific "
                                  "questions from federal passages.")
        for i, why in results:
            c = self.chunks[i]
            out["passages"].append({
                "chunk_id": c.chunk_id, "doc_id": c.doc_id, "title": c.title, "paragraph": c.label,
                "jurisdiction": c.jurisdiction,
                "scope": "federal (all states)" if c.jurisdiction in FEDERAL else f"state-specific ({c.jurisdiction})",
                "doc_type": c.meta.get("doc_type"), "published": c.meta.get("published"),
                "effective_date": c.meta.get("effective_date") or None,
                "supersedes": c.meta.get("supersedes") or None, "superseded_by": self.superseded_by.get(c.doc_id),
                "source_url": c.meta.get("url"), "selected_by": why,
                "scores": {"dense": round(dense[i], 3) if i in dense else None,
                           "bm25_rank": bm25_rank.get(i), "dense_rank": dense_rank.get(i)},
                "text": c.text[:900]})
        return out


# Backwards-compatible helper used by long-term memory ingestion (document-level metadata).
@dataclass
class PolicyDoc:
    meta: dict
    title: str
    body: str

    @property
    def doc_id(self) -> str:
        return self.meta["doc_id"]


def load_corpus(policy_dir: Path = POLICY_DIR) -> list[PolicyDoc]:
    docs = []
    for path in sorted(Path(policy_dir).glob("*.md")):
        meta, title, paras = _parse_md(path)
        docs.append(PolicyDoc(meta, title, "\n\n".join(paras)))
    return docs
