"""Human oversight (Week 6; Checkpoint 6.1 + grader feedback).

Escalation policy: deterministic triggers evaluated on the finished run (question, answer,
evidence, stop reason, monitor score). MANDATORY triggers withhold the conclusion until a human
reviewer approves; RECOMMENDED triggers release the answer and queue a review.

  MANDATORY  source_conflict      equal-authority sources disagree on a policy fact (status "conflicted")
  MANDATORY  unresolved_date      the policy date behind an analysis is unknown or needs review
  MANDATORY  decision_sensitive   the question asks for an eligibility, payment, coverage-determination,
                                  or compliance decision about a case
  MANDATORY  causal_question      the user asks whether a policy CAUSED a change (methodological review)
  RECOMMENDED unverified          automatic verification did not fully pass, or the run hit a limit
  RECOMMENDED monitor_flag        the trajectory monitor's suspicion score is at or above the threshold
  RECOMMENDED low_confidence      the agent itself reported low confidence on a substantive answer

Review queue: SQLite (runs/review_queue.sqlite), PII-redacted. A reviewer approves, revises, or
rejects. A withheld conclusion waits at most its SLA (feedback: "how long can it wait?"):
  decision_sensitive 24 h · source_conflict / unresolved_date / causal_question 72 h · recommended 7 days.
At expiry the ticket is marked "expired" and the draft is NEVER released automatically (fail closed);
the user is told no reviewed answer is available.

Reviewer metrics (feedback: "do the triggers work?"): approval / revision / rejection rates,
median hours to decision, and expiry rate, per trigger. A trigger whose drafts are almost always
approved unchanged is a candidate for relaxation; one with frequent revisions is doing its job.
"""

from __future__ import annotations

import json
import re
import sqlite3
import statistics
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .guardrails import redact
from .paths import RUNS_DIR

QUEUE_PATH = RUNS_DIR / "review_queue.sqlite"
MANDATORY = {"source_conflict", "unresolved_date", "decision_sensitive", "causal_question"}
SLA_HOURS = {"decision_sensitive": 24, "source_conflict": 72, "unresolved_date": 72, "causal_question": 72,
             "unverified": 168, "monitor_flag": 168, "low_confidence": 168, "background_conflict": 168}
TIMING_QUESTION = re.compile(r"\bwhen\b|\beffective\b|\btake[sn]? effect\b|\btook effect\b|\bstart(?:ed|s)?\b|"
                             r"\bbegin|\bbegan\b|\bdate\b|\bafter (?:it|the)\b", re.I)

DECISION_SENSITIVE = re.compile(
    r"\b(?:should (?:we|i|the (?:state|plan|agency))|can (?:we|i)|do (?:we|i) need to|are we required to)\b"
    r"[^?.]{0,80}\b(?:deny|approve|deny|pay|reimburse|recoup|cover|terminate|disenroll|flag|audit|sanction)\b"
    r"|\bis (?:this|the|my|our) (?:member|patient|beneficiary|claim|provider)\b[^?.]{0,60}\b(?:eligible|covered|"
    r"compliant|approvable|payable)\b"
    r"|\b(?:eligibility determination|coverage determination|overpayment|recoupment|compliance (?:finding|decision))\b",
    re.I,
)
CAUSAL_QUESTION = re.compile(
    r"\b(?:did|does|has|have|was|were)\b[^?]{0,80}\b(?:cause|caused|causing)\b|\bcausal(?:ly)?\b"
    r"|\b(?:impact|effect) of (?:the |its |this |that )?(?:policy|prior authori[sz]ation|PA|requirement|coverage|"
    r"amendment|change)\b|\battributable to\b|\bbecause of the (?:policy|prior authori[sz]ation|PA|requirement)\b",
    re.I,
)


@dataclass
class OversightDecision:
    escalated: bool
    mandatory: bool
    triggers: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    released: bool = True
    ticket_id: str | None = None
    sla_hours: int | None = None
    delivered_message: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def evaluate_triggers(question: str, answer: dict, evidence: list, stop_reason: str, verified: bool,
                      monitor: dict | None = None) -> OversightDecision:
    triggers, reasons = [], []
    payloads = [(e.kind, e.payload) for e in evidence] if evidence and hasattr(evidence[0], "payload") else \
        [(e.get("tool"), e.get("payload", {})) for e in (evidence or [])]
    answered = answer.get("answer_type") == "answer"

    used_analysis = any(k in ("pre_post_analysis",) for k, _ in payloads)
    asks_timing = bool(TIMING_QUESTION.search(question or ""))
    for kind, p in payloads:
        if kind == "get_policy_timeline" and p.get("status") == "conflicted":
            # Mandatory only if the answer DEPENDS on the disputed fact. Live eval: a PA question that
            # merely looked up a conflicted date was withheld unnecessarily (the unnecessary-escalation
            # metric caught it).
            if used_analysis or asks_timing:
                triggers.append("source_conflict")
                reasons.append(f"Sources of equal authority disagree on {p.get('key')}, and the answer depends on it.")
            else:
                triggers.append("background_conflict")
                reasons.append(f"A source conflict exists on {p.get('key')} but the answer does not rely on it; "
                               "disclosed and queued for routine review.")
    if used_analysis:
        for kind, p in payloads:
            if kind == "get_policy_timeline" and p.get("status") in ("needs_review", "conflicted", "unknown"):
                triggers.append("unresolved_date")
                reasons.append(f"Policy date for {p.get('key')} is {p.get('status')}.")
    if DECISION_SENSITIVE.search(question or ""):
        triggers.append("decision_sensitive")
        reasons.append("The question asks for an eligibility, payment, coverage, or compliance decision about a case.")
    if CAUSAL_QUESTION.search(question or "") and answered:
        triggers.append("causal_question")
        reasons.append("The question asks whether a policy caused a change; pre/post data shows association only.")
    if answered and (not verified or stop_reason not in ("submitted", "guardrail", "clarification")):
        triggers.append("unverified")
        reasons.append(f"Verification incomplete (verified={verified}, stop={stop_reason}).")
    if monitor and monitor.get("flagged"):
        triggers.append("monitor_flag")
        reasons.append(f"Trajectory monitor score {monitor.get('score')} >= {monitor.get('threshold')}.")
    if answered and str(answer.get("confidence", "")).lower() == "low":
        triggers.append("low_confidence")
        reasons.append("The agent reported low confidence.")

    triggers = list(dict.fromkeys(triggers))
    mandatory = any(t in MANDATORY for t in triggers)
    return OversightDecision(bool(triggers), mandatory, triggers, reasons, released=not mandatory,
                             sla_hours=min((SLA_HOURS[t] for t in triggers), default=None))


# ----------------------------------------------------------------------------- queue
class ReviewQueue:
    def __init__(self, path: Path = QUEUE_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._con() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY, created_at TEXT, sla_due_at TEXT, question TEXT, triggers TEXT,
                reasons TEXT, mandatory INTEGER, draft TEXT, evidence TEXT, status TEXT,
                reviewer TEXT, decided_at TEXT, note TEXT, final_answer TEXT, run_label TEXT)""")

    def _con(self):
        return sqlite3.connect(self.path)

    def submit(self, question: str, answer: dict, evidence: list, decision: OversightDecision,
               run_label: str = "") -> str:
        now = datetime.now(timezone.utc)
        tid = f"RV-{uuid.uuid4().hex[:6].upper()}"
        ev = [{"id": e.id, "tool": e.kind, "summary": redact(e.summary)} if hasattr(e, "id") else e for e in evidence]
        with self._con() as c:
            c.execute("INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                tid, now.isoformat(timespec="seconds"),
                (now + timedelta(hours=decision.sla_hours or 72)).isoformat(timespec="seconds"),
                redact(question), json.dumps(decision.triggers), json.dumps(decision.reasons),
                int(decision.mandatory), redact(json.dumps(answer)), json.dumps(ev)[:20000], "pending",
                None, None, None, None, run_label))
        return tid

    def pending(self) -> list[dict]:
        self.expire_overdue()
        return self.list(status="pending")

    def list(self, status: str | None = None) -> list[dict]:
        with self._con() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute("SELECT * FROM tickets" + (" WHERE status = ?" if status else "")
                             + " ORDER BY created_at DESC", (status,) if status else ()).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for k in ("triggers", "reasons", "draft", "evidence", "final_answer"):
                d[k] = json.loads(d[k]) if d.get(k) else None
            out.append(d)
        return out

    def get(self, tid: str) -> dict | None:
        return next((t for t in self.list() if t["id"] == tid), None)

    def decide(self, tid: str, decision: str, reviewer: str, note: str = "", revised_answer: dict | None = None) -> dict:
        if decision not in ("approved", "revised", "rejected"):
            raise ValueError("decision must be approved, revised, or rejected")
        if decision == "revised" and not revised_answer:
            raise ValueError("a revision needs the revised answer")
        t = self.get(tid)
        if t is None or t["status"] != "pending":
            raise ValueError(f"ticket {tid} is not pending")
        final = revised_answer if decision == "revised" else (t["draft"] if decision == "approved" else None)
        with self._con() as c:
            c.execute("UPDATE tickets SET status=?, reviewer=?, decided_at=?, note=?, final_answer=? WHERE id=?",
                      (decision, reviewer, datetime.now(timezone.utc).isoformat(timespec="seconds"), redact(note),
                       json.dumps(final) if final else None, tid))
        return self.get(tid)

    def expire_overdue(self, now: datetime | None = None) -> int:
        """Fail closed: overdue tickets expire and their draft is never auto-released."""
        now = now or datetime.now(timezone.utc)
        with self._con() as c:
            cur = c.execute("UPDATE tickets SET status='expired', decided_at=? WHERE status='pending' AND sla_due_at < ?",
                            (now.isoformat(timespec="seconds"), now.isoformat(timespec="seconds")))
            return cur.rowcount

    def released_answer(self, tid: str) -> dict:
        """What the user may see for a ticket right now."""
        t = self.get(tid)
        if t is None:
            return {"status": "unknown"}
        if t["status"] in ("approved", "revised"):
            return {"status": t["status"], "answer": t["final_answer"], "reviewer_note": t["note"]}
        if t["status"] == "pending":
            return {"status": "pending", "message": f"Withheld pending human review (due {t['sla_due_at']} UTC)."}
        return {"status": t["status"], "message": "No reviewed answer is available; the draft was not released."}

    def metrics(self) -> dict:
        tickets = self.list()
        decided = [t for t in tickets if t["status"] in ("approved", "revised", "rejected")]
        n = len(decided)
        hours = [(datetime.fromisoformat(t["decided_at"]) - datetime.fromisoformat(t["created_at"])).total_seconds() / 3600
                 for t in decided if t.get("decided_at")]
        by_trigger: dict[str, dict] = {}
        for t in tickets:
            for trig in t["triggers"] or []:
                b = by_trigger.setdefault(trig, {"total": 0, "approved": 0, "revised": 0, "rejected": 0,
                                                 "expired": 0, "pending": 0})
                b["total"] += 1
                b[t["status"]] = b.get(t["status"], 0) + 1
        rate = lambda s: round(sum(t["status"] == s for t in decided) / n, 3) if n else None
        return {"tickets": len(tickets), "pending": sum(t["status"] == "pending" for t in tickets),
                "decided": n, "approval_rate": rate("approved"), "revision_rate": rate("revised"),
                "rejection_rate": rate("rejected"),
                "expired": sum(t["status"] == "expired" for t in tickets),
                "median_hours_to_decision": round(statistics.median(hours), 2) if hours else None,
                "by_trigger": by_trigger}


def apply_oversight(result, queue: ReviewQueue | None = None, monitor: dict | None = None,
                    evidence_objects: list | None = None, run_label: str = ""):
    """Evaluate triggers on a finished AgentResult, queue it if needed, and set what the user receives.
    The draft stays in result.answer (for grading and audit); result.oversight says whether it is released."""
    decision = evaluate_triggers(result.question, result.answer, evidence_objects or [], result.stop_reason,
                                 result.verified, monitor)
    if decision.escalated and queue is not None:
        decision.ticket_id = queue.submit(result.question, result.answer, evidence_objects or result.evidence,
                                          decision, run_label)
    if decision.mandatory:
        decision.delivered_message = (
            "This conclusion is WITHHELD pending human review "
            f"({', '.join(t for t in decision.triggers if t in MANDATORY)}). "
            + " ".join(decision.reasons[:2])
            + (f" Ticket {decision.ticket_id}; a reviewer must approve, revise, or reject it within "
               f"{decision.sla_hours} h, otherwise no answer is released." if decision.ticket_id else ""))
    elif decision.escalated:
        decision.delivered_message = (f"Released, and queued for routine review ({', '.join(decision.triggers)})."
                                      + (f" Ticket {decision.ticket_id}." if decision.ticket_id else ""))
    result.oversight = decision.to_dict()
    return result
