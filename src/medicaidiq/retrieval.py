"""Policy document retrieval.

Week 2: BM25 keyword search plus metadata filters (state, as-of date). Keyword
matching matters here because document IDs and drug names must match exactly
(D1.3). Week 3 adds vector search to make it hybrid.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .paths import POLICY_DIR

_TOKEN = re.compile(r"[a-z0-9][a-z0-9\-]*")
_STOP = {"the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "is", "be", "with", "by",
         "this", "that", "are", "was", "as", "at", "from", "will", "all", "any", "what", "when", "did"}


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


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
        raw = path.read_text(encoding="utf-8")
        _, front, body = raw.split("---", 2)
        meta = dict(line.split(": ", 1) if ": " in line else (line.rstrip(":"), "")
                    for line in front.strip().splitlines())
        title_match = re.search(r"^# (.+)$", body, re.MULTILINE)
        text = re.sub(r"^>.*$|^# .+$", "", body, flags=re.MULTILINE).strip()
        docs.append(PolicyDoc(meta, title_match.group(1) if title_match else meta["doc_id"], text))
    return docs


class PolicySearch:
    def __init__(self, docs: list[PolicyDoc] | None = None, k1: float = 1.5, b: float = 0.75):
        self.docs = docs if docs is not None else load_corpus()
        self.k1, self.b = k1, b
        self.tokens = [tokenize(f"{d.title} {d.title} {d.body} {d.meta.get('topic', '')} "
                                f"{d.meta.get('drug_class', '')} {d.doc_id}") for d in self.docs]
        self.avgdl = sum(map(len, self.tokens)) / max(len(self.tokens), 1)
        df = Counter(t for toks in self.tokens for t in set(toks))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        superseders = {d.meta.get("supersedes"): d.doc_id for d in self.docs if d.meta.get("supersedes")}
        self.superseded_by = superseders

    def _score(self, q: list[str], i: int) -> float:
        tf = Counter(self.tokens[i])
        dl = len(self.tokens[i])
        s = 0.0
        for t in q:
            if t in tf:
                s += self.idf.get(t, 0) * tf[t] * (self.k1 + 1) / (
                    tf[t] + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
        return s

    def search(self, query: str, state: str | None = None, as_of: str | None = None,
               top_k: int = 4) -> list[dict]:
        q = tokenize(query)
        hits = []
        for i, d in enumerate(self.docs):
            st = d.meta.get("state", "")
            if state and st not in (state.upper(), "ALL"):
                continue
            if as_of and d.meta.get("published", "") > as_of:
                continue  # document did not exist yet at the as-of date
            score = self._score(q, i)
            if score > 0:
                hits.append((score, d))
        hits.sort(key=lambda h: -h[0])
        return [{
            "doc_id": d.doc_id, "title": d.title, "state": d.meta.get("state"),
            "doc_type": d.meta.get("doc_type"), "published": d.meta.get("published"),
            "effective_date": d.meta.get("effective_date"),
            "supersedes": d.meta.get("supersedes") or None,
            "superseded_by": self.superseded_by.get(d.doc_id),
            "score": round(s, 3), "text": d.body[:700],
        } for s, d in hits[:top_k]]
