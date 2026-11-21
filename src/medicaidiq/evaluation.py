"""Evaluation harness (D1.6): model-agnostic, logs quality + cost + latency per case.

Each case is graded with deterministic checks, no LLM judge:
  answer_type       : expected outcome (answer / cannot_answer / clarification_needed)
  required_tools    : the agent used the tool that grounds the answer
  effective_date    : pre/post used the authoritative date (supersession/conflict traps)
  number            : the reported headline number matches the deterministic tool value (+-0.6)
  mentions          : required disclosures appear (caveats, conflicts, missing quarters)
  no_fabrication    : abstention cases contain no analytic numbers
  verified          : the answer passed the grounding verifier
  tool_call_valid   : no malformed tool calls (invalid JSON / unknown tool)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .agent import MedicaidIQAgent
from .db import ReadOnlyDB
from .tools import Toolbox
from .verify import _claim_numbers

CASES_PATH = Path(__file__).resolve().parents[2] / "eval" / "cases.jsonl"


def load_cases(path: Path = CASES_PATH, ids: list[str] | None = None) -> list[dict]:
    cases = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [c for c in cases if not ids or c["id"] in ids]


def _answer_text(answer: dict) -> str:
    parts = [answer.get("summary", ""), answer.get("confidence_reason", "")]
    parts += [f.get("statement", "") for f in answer.get("findings", [])]
    parts += answer.get("caveats", []) + answer.get("human_review", [])
    return " ".join(p for p in parts if isinstance(p, str))


def _expected_number(case: dict, toolbox: Toolbox) -> float | None:
    if "expect_number" in case:
        spec = case["expect_number"]
        from .memory import WorkingMemory
        obs = toolbox.execute(spec["tool"], json.dumps(spec["args"]), WorkingMemory("grader"))
        return obs.get(spec["field"])
    if "expect_sql_value" in case:
        return toolbox.db.query_trusted(case["expect_sql_value"]).rows[0][0]
    return None


def grade(case: dict, result, toolbox: Toolbox) -> dict:
    checks: dict[str, bool] = {}
    answer = result.answer
    text = _answer_text(answer)
    low = text.lower()
    actions = [t.action for t in result.trace]

    checks["answer_type"] = answer.get("answer_type") in case["expect_type"]
    if case.get("required_tools"):
        ok_calls = {t.action for t in result.trace if "error" not in t.observation}
        checks["required_tools"] = all(t in ok_calls for t in case["required_tools"])
    if case.get("effective_date"):
        dates = [t.arguments.get("effective_date") for t in result.trace if t.action == "pre_post_analysis"]
        checks["effective_date"] = bool(dates) and dates[-1] == case["effective_date"]
    expected = _expected_number(case, toolbox)
    if expected is not None:
        nums = [v for v, _ in _claim_numbers(text)]
        checks["number"] = any(abs(abs(n) - abs(expected)) <= 0.6 or
                               (abs(expected) > 1000 and abs(n - expected) / abs(expected) < 0.001)
                               for n in nums)
    if case.get("must_mention_any"):
        checks["mentions_any"] = any(k.lower() in low for k in case["must_mention_any"])
    if case.get("must_mention_all"):
        checks["mentions_all"] = all(k.lower() in low for k in case["must_mention_all"])
    if case.get("must_cite_any"):       # retrieval grounding: answer cites a specific passage/document
        checks["cites_source"] = any(c.lower() in low for c in case["must_cite_any"])
    if case.get("must_not_mention"):    # e.g. cross-state contamination
        checks["no_forbidden"] = not any(k.lower() in low for k in case["must_not_mention"])
    if case.get("forbid_numbers"):
        checks["no_fabrication"] = not [v for v, _ in _claim_numbers(answer.get("summary", ""))
                                        + sum((_claim_numbers(f.get("statement", ""))
                                               for f in answer.get("findings", [])), [])]
    if answer.get("answer_type") == "answer":
        checks["verified"] = bool(result.verified)
    malformed = [t for t in result.trace if isinstance(t.observation, dict) and
                 str(t.observation.get("error", "")).startswith(("Invalid JSON", "Unknown tool", "Bad arguments"))]
    checks["tool_call_valid"] = not malformed and "(no tool call)" not in actions
    return {"id": case["id"], "category": case["category"], "passed": all(checks.values()),
            "checks": checks, "answer_type": answer.get("answer_type"), "stop_reason": result.stop_reason,
            "steps": result.steps, "actions": actions, "expected_number": expected,
            "summary": answer.get("summary", ""), "usage": result.usage}


@dataclass
class EvalRun:
    label: str
    results: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        n = len(self.results)
        check_names = sorted({k for r in self.results for k in r["checks"]})
        per_check = {}
        for c in check_names:
            vals = [r["checks"][c] for r in self.results if c in r["checks"]]
            per_check[c] = f"{sum(vals)}/{len(vals)}"
        cost = sum(r["usage"].get("cost_usd", 0) for r in self.results)
        secs = [r["wall_seconds"] for r in self.results]
        toks_in = sum(r["usage"].get("prompt_tokens", 0) for r in self.results)
        toks_out = sum(r["usage"].get("completion_tokens", 0) for r in self.results)
        return {"label": self.label, "cases": n, "passed": sum(r["passed"] for r in self.results),
                "pass_rate": round(sum(r["passed"] for r in self.results) / n, 3) if n else 0,
                "checks": per_check, "total_cost_usd": round(cost, 4),
                "avg_cost_per_question_usd": round(cost / n, 5) if n else 0,
                "avg_prompt_tokens": round(toks_in / n) if n else 0,
                "avg_completion_tokens": round(toks_out / n) if n else 0,
                "avg_seconds": round(sum(secs) / n, 1) if n else 0,
                "max_seconds": round(max(secs), 1) if secs else 0,
                "consistency": _consistency(self.results)}


def _consistency(results: list[dict]) -> str:
    by_id = {r["id"]: r for r in results}
    pairs = [(r, by_id.get(r.get("paraphrase_of"))) for r in results if r.get("paraphrase_of")]
    pairs = [(a, b) for a, b in pairs if b]
    if not pairs:
        return "n/a"
    same = sum(a["answer_type"] == b["answer_type"] and a["checks"].get("number") == b["checks"].get("number")
               for a, b in pairs)
    return f"{same}/{len(pairs)} paraphrase pairs consistent"


def run_eval(llm_factory, label: str, ids: list[str] | None = None, max_steps: int = 10,
             progress=print, retrieval: bool = True, category: str | None = None,
             planning: bool = False) -> EvalRun:
    db = ReadOnlyDB()
    run = EvalRun(label)
    cases = [c for c in load_cases(ids=ids) if not category or c["category"].startswith(category)]
    for case in cases:
        llm = llm_factory()
        agent = MedicaidIQAgent(llm, Toolbox(db), max_steps=max_steps, retrieval=retrieval, planning=planning)
        t0 = time.perf_counter()
        try:
            result = agent.run(case["question"])
            graded = grade(case, result, Toolbox(db))
            graded["trace"] = [{"thought": t.thought[:300], "action": t.action, "arguments": t.arguments,
                                "observation": json.dumps(t.observation, default=str)[:600]}
                               for t in result.trace]
            graded["answer"] = result.answer
        except Exception as exc:  # provider/network failure is a reliability result, not a crash
            graded = {"id": case["id"], "category": case["category"], "passed": False,
                      "checks": {"completed": False}, "error": f"{type(exc).__name__}: {exc}"[:400],
                      "usage": {}, "answer_type": None, "steps": 0, "actions": []}
        graded["wall_seconds"] = round(time.perf_counter() - t0, 1)
        graded["paraphrase_of"] = case.get("paraphrase_of")
        run.results.append(graded)
        progress(f"  {case['id']:<24} {'PASS' if graded['passed'] else 'FAIL'}  "
                 f"{graded['wall_seconds']:>6}s  {graded.get('checks')}")
    return run
