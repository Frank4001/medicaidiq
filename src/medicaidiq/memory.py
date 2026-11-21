"""Agent memory (Week 2): short-term working memory and a long-term knowledge store.

Short-term (WorkingMemory) lives for one question. It holds the analysis slots
(state, drug class, policy date, periods), every observation as numbered evidence
(E1, E2, ...) that the final answer must cite, and failed attempts so the agent
doesn't repeat them.

Long-term (KnowledgeStore) persists across questions. It holds facts derived from
source documents, such as a policy's effective date, with full provenance. It
answers the Week 2 grader's questions:

* **Supersession.** A document that explicitly `supersedes` another marks the
  older document's facts `superseded` (kept for audit, never used as evidence).
* **Reconciling conflicting versions.** Among live facts for the same key, the
  higher-authority source wins (AUTHORITY below). The losing facts stay attached
  as `conflicts`, so the answer can disclose the disagreement. Equal authority
  with different values means status `conflicted`: the agent must not choose and
  must flag the fact for human review.
* **Update triggers.** (1) Ingesting a document re-resolves every key it
  touches. (2) A fact not verified within `review_days` is reported as
  `needs_review`. (3) Only a human `approve()` promotes a `provisional` fact (for
  example an agent interpretation) to `verified`, so an earlier agent mistake
  can't become evidence.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

from .paths import KNOWLEDGE_PATH

# Source authority hierarchy (Week 1 decision D1.13). Higher wins.
AUTHORITY = {
    "statute_or_regulation": 5,
    "state_plan_amendment": 4,
    "waiver": 4,
    "federal_guidance": 3,
    "provider_manual": 2,
    "provider_bulletin": 1,
    "agent_interpretation": 0,
}


_EVIDENCE_LOCK = threading.Lock()


# ----------------------------------------------------------------------------- short-term
@dataclass
class Evidence:
    id: str
    kind: str          # tool name that produced it
    summary: str
    payload: dict


@dataclass
class WorkingMemory:
    question: str
    slots: dict = field(default_factory=dict)        # state, drug_class, effective_date, ...
    evidence: list[Evidence] = field(default_factory=list)
    failed_attempts: list[str] = field(default_factory=list)

    def add_evidence(self, kind: str, summary: str, payload: dict) -> str:
        with _EVIDENCE_LOCK:   # agents may write concurrently in the multi-agent workflow
            eid = f"E{len(self.evidence) + 1}"
            self.evidence.append(Evidence(eid, kind, summary, payload))
        return eid

    def record_failure(self, description: str) -> None:
        self.failed_attempts.append(description)

    def update_slots(self, **kwargs) -> None:
        self.slots.update({k: v for k, v in kwargs.items() if v not in (None, "")})

    def numbers_in_evidence(self) -> set[float]:
        """Every number appearing in any observation, used for the grounding check."""
        nums: set[float] = set()
        for ev in self.evidence:
            nums.update(extract_numbers(json.dumps(ev.payload)))
        return nums

    def scratchpad(self) -> str:
        """Compact state summary re-injected each step so long tool outputs can't bury it."""
        lines = [f"Slots so far: {json.dumps(self.slots) if self.slots else '(none)'}"]
        if self.evidence:
            lines.append("Evidence: " + "; ".join(f"{e.id}={e.summary}" for e in self.evidence[-8:]))
        if self.failed_attempts:
            lines.append("Failed attempts (do not repeat): " + "; ".join(self.failed_attempts[-5:]))
        return "\n".join(lines)


_NUM = re.compile(r"-?\d[\d,]*\.?\d*")


def extract_numbers(text: str) -> set[float]:
    out = set()
    for m in _NUM.findall(text):
        try:
            out.add(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


# ----------------------------------------------------------------------------- long-term
@dataclass
class Fact:
    key: str                 # e.g. "EV:glp1_prior_auth:effective_date"
    value: str
    source_doc: str
    doc_type: str
    published: str
    status: str = "verified"  # verified | provisional | superseded
    superseded_by: str = ""
    last_verified: str = field(default_factory=lambda: date.today().isoformat())
    note: str = ""

    @property
    def authority(self) -> int:
        return AUTHORITY.get(self.doc_type, 0)


@dataclass
class Resolution:
    key: str
    status: str               # resolved | conflicted | needs_review | unknown
    value: str | None
    source_doc: str | None
    conflicts: list[dict]
    superseded: list[dict]
    explanation: str

    def to_dict(self) -> dict:
        return asdict(self)


class KnowledgeStore:
    def __init__(self, path: Path = KNOWLEDGE_PATH, review_days: int = 365, today: date | None = None):
        self.path = Path(path)
        self.review_days = review_days
        self.today = today or date.today()
        self.facts: list[Fact] = []
        if self.path.exists():
            self.facts = [Fact(**f) for f in json.loads(self.path.read_text(encoding="utf-8"))]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([asdict(f) for f in self.facts], indent=2), encoding="utf-8")

    # -- writes -------------------------------------------------------------------------------
    def ingest_document(self, meta: dict) -> list[str]:
        """Record the facts a policy document asserts, then apply supersession. Returns keys touched."""
        keys = []
        if meta.get("effective_date") and meta.get("state") not in (None, "", "ALL"):
            key = f"{meta['state']}:{meta['topic']}:effective_date"
            self._upsert(Fact(key=key, value=meta["effective_date"], source_doc=meta["doc_id"],
                              doc_type=meta["doc_type"], published=meta["published"]))
            keys.append(key)
        if meta.get("supersedes"):
            for f in self.facts:
                if f.source_doc == meta["supersedes"] and f.status != "superseded":
                    f.status, f.superseded_by = "superseded", meta["doc_id"]
                    f.note = f"Replaced by {meta['doc_id']} (published {meta['published']})."
        return keys

    def add_interpretation(self, key: str, value: str, note: str) -> None:
        """Agent-derived knowledge enters only as provisional (authority 0) until a human approves."""
        self._upsert(Fact(key=key, value=value, source_doc="agent", doc_type="agent_interpretation",
                          published=self.today.isoformat(), status="provisional", note=note))

    def approve(self, key: str, value: str, reviewer: str) -> bool:
        for f in self.facts:
            if f.key == key and f.value == value and f.status == "provisional":
                f.status, f.last_verified = "verified", self.today.isoformat()
                f.note += f" Approved by {reviewer} on {self.today.isoformat()}."
                return True
        return False

    def _upsert(self, fact: Fact) -> None:
        self.facts = [f for f in self.facts if not (f.key == fact.key and f.source_doc == fact.source_doc)]
        self.facts.append(fact)

    # -- reads --------------------------------------------------------------------------------
    def resolve(self, key: str) -> Resolution:
        all_facts = [f for f in self.facts if f.key == key]
        live = [f for f in all_facts if f.status == "verified"]
        superseded = [asdict(f) for f in all_facts if f.status == "superseded"]
        if not live:
            return Resolution(key, "unknown", None, None, [], superseded,
                              "No verified fact on record; retrieve source documents.")
        top = max(f.authority for f in live)
        winners = [f for f in live if f.authority == top]
        losers = [asdict(f) for f in live if f.authority < top and f.value != winners[0].value]
        if len({f.value for f in winners}) > 1:
            return Resolution(key, "conflicted", None, None, [asdict(f) for f in winners], superseded,
                              "Sources of equal authority disagree; do not choose. Flag for human review.")
        best = max(winners, key=lambda f: f.published)
        age = (self.today - datetime.fromisoformat(best.last_verified).date()).days
        status = "needs_review" if age > self.review_days else "resolved"
        expl = f"{best.source_doc} ({best.doc_type}) is the highest-authority current source."
        if losers:
            expl += (" Lower-authority sources disagree: "
                     + ", ".join(f"{l['source_doc']} says {l['value']}" for l in losers)
                     + ". Disclose this conflict.")
        if superseded:
            expl += " Superseded: " + ", ".join(f"{s['source_doc']} ({s['value']})" for s in superseded) + "."
        if status == "needs_review":
            expl += f" Last verified {age} days ago; re-verify before relying on it."
        return Resolution(key, status, best.value, best.source_doc, losers, superseded, expl)
