"""Answer verification: the agent's self-check before an answer reaches the user.

Checks:
  1. Number grounding (D1.2): every number in the summary/findings must appear in some tool
     observation (allowing for rounding and sign words like "fell 18%" for -17.9).
  2. Citation validity: every cited evidence id exists.
  3. Substantive answers must cite at least one piece of evidence.
"""

from __future__ import annotations

import re

from .memory import WorkingMemory

# Numbers that are structural, not claims: years, quarter labels, small ordinals, CI level, and
# citations (42 CFR 430.20(b), § 447.512, chunk ids like 42-CFR-456.705(b)).
_IGNORE = re.compile(r"\b\d+[\s-]*CFR[\s-]*(?:(?:part|§)\s*)?\d+(?:\.\d+)?(?:\([a-z0-9]+\))*(?:-\d+)?"
                     r"|§+\s*\d+(?:\.\d+)?(?:\([a-z0-9]+\))*"
                     r"|\b(19|20)\d{2}(?:-\d{2}-\d{2})?\b|\b\d{4}Q[1-4]\b|\bQ[1-4]\b|\bE\d+\b|95%|\b1,000\b",
                     re.IGNORECASE)
_NUM = re.compile(r"-?\$?\d[\d,]*(?:\.\d+)?")


def _claim_numbers(text: str) -> list[tuple[float, int]]:
    """Return (value, decimals) for each number that represents a claim."""
    cleaned = _IGNORE.sub(" ", text)
    out = []
    for tok in _NUM.findall(cleaned):
        tok = tok.replace("$", "").replace(",", "")
        try:
            val = float(tok)
        except ValueError:
            continue
        if abs(val) <= 10 and float(val).is_integer():
            continue  # counts like "4 quarters", "2 states" are not analytic claims
        decimals = len(tok.split(".")[1]) if "." in tok else 0
        out.append((val, decimals))
    return out


def _grounded(val: float, decimals: int, evidence: set[float]) -> bool:
    tol = 0.5 * 10 ** (-decimals) + 1e-9
    for e in evidence:
        if abs(abs(e) - abs(val)) <= tol:
            return True
        # allow rounding to thousands/millions, e.g. "$1.2 million" style is avoided by prompt
    return False


def verify_answer(answer: dict, memory: WorkingMemory) -> list[str]:
    problems: list[str] = []
    valid_ids = {e.id for e in memory.evidence}
    text_parts = [answer.get("summary", "")] + [f.get("statement", "") for f in answer.get("findings", [])]
    evidence_nums = memory.numbers_in_evidence()
    ungrounded = sorted({str(v) for part in text_parts for v, d in _claim_numbers(part)
                         if not _grounded(v, d, evidence_nums)})
    if ungrounded:
        problems.append(f"Numbers not found in any tool result: {', '.join(ungrounded)}. "
                        "Compute them with a tool (calculate/pre_post_analysis) or remove them.")
    for f in answer.get("findings", []):
        bad = [i for i in f.get("evidence_ids", []) if i not in valid_ids]
        if bad:
            problems.append(f"Finding cites unknown evidence ids {bad}; valid ids: {sorted(valid_ids)}.")
    if answer.get("answer_type") == "answer":
        if not answer.get("findings"):
            problems.append("An 'answer' must include at least one finding with evidence ids.")
        elif not any(f.get("evidence_ids") for f in answer["findings"]):
            problems.append("Findings must cite evidence ids.")
    return problems
