"""Tree-of-Thought analysis planner (Week 4).

Where ToT is used: choosing HOW to analyze a before/after, spending-change, or period-
comparison question, before any analysis runs. A linear agent commits to the first plan
it thinks of (raw counts instead of per-member rates; the announced date instead of the
effective one; a window that includes provisional quarters). Lookups and policy-text
questions skip ToT and stay on the plain ReAct path.

ToT structure (Yao et al. 2023; Checkpoint 4.1 design):
  thought  = a structured analysis-plan proposal (JSON), refined level by level
  depth 1  = measure + population (metric, state, drug class, denominator)
  depth 2  = comparison design (effective date + source, windows, required checks)
  depth 3  = executable plan (ordered tool steps) + interpretation limits
  branching: <= 3 children per node (propose strategy, one LLM call per parent)
  search   : beam search, width 2  -> at most 3 + 2*3 + 2*3 = 15 nodes
  stop     : depth 3 reached | budget exhausted | no surviving candidates

Evaluation (10-point rubric). Deterministic tool checks where possible (Lab 4.1's
Python-backed verification), LLM critic only where judgment is needed:
  scope alignment      0-3  deterministic: valid state, drug class, metric fits the question
  evidence support     0-3  deterministic: date matches the verified policy timeline + source cited
  feasibility          0-2  deterministic: windows have enough FINAL quarters; steps are valid tools
  interpretation       0-2  LLM critic: answers the actual question, avoids causal overclaim
  Partial plans are scored on the criteria applicable so far, normalized to 10.
  Prune below 7. Hard failures (unknown state, superseded date, no usable quarters,
  invalid tool) prune regardless of score. Ties: evidence, then scope, then fewer steps.
  Mitigation for premature pruning: a branch weak ONLY for missing evidence gets one
  targeted tool check (policy timeline / data coverage) before it can be pruned.

Budget: 30 model calls, 20 tool calls, 60 s (proposed limits from Checkpoint 4.1).
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from . import stats
from .memory import WorkingMemory
from .paths import RUNS_DIR
from .tools import TOOL_SCHEMAS, Toolbox

STATES = {"NL", "EV", "SP", "IL"}
DRUGS = {"GLP1", "SGLT2", "INSULIN"}
METRICS = set(stats.METRICS) | {"spending_decomposition"}
SPEND_METRICS = {"spend_pmpm", "spend_per_rx", "spending_decomposition"}
STEP_TOOLS = {"get_policy_timeline", "check_data_coverage", "pre_post_analysis", "spending_decomposition",
              "run_sql", "search_policy", "calculate"}
REQUIRED_ARGS = {t["function"]["name"]: t["function"]["parameters"]["required"] for t in TOOL_SCHEMAS}
PLANS_DIR = RUNS_DIR / "plans"
POLICY_WORDS = re.compile(r"polic|authoriz|\bPA\b|coverage|amendment|took effect|requirement|bulletin|rule",
                          re.IGNORECASE)


@dataclass
class Budget:
    model_calls: int = 30
    tool_calls: int = 20
    seconds: float = 60.0


@dataclass
class PlanNode:
    id: str
    parent: str | None
    depth: int
    plan: dict
    score: float = 0.0
    breakdown: dict = field(default_factory=dict)
    hard_failures: list = field(default_factory=list)
    needs_evidence: bool = False
    critic_note: str = ""
    status: str = "candidate"     # candidate | kept | pruned | selected
    prune_reason: str = ""


GEN_SYSTEM = ("You are the PLANNER in a Tree-of-Thought search over analysis plans for Medicaid drug-utilization "
              "questions. Propose genuinely DIFFERENT alternatives (different metrics, denominators, dates, or "
              "windows), not paraphrases. Never compute numbers. Be concise: every string under 20 words, at most "
              "2 assumptions per candidate. Reply with a compact JSON object only.")
CRITIC_SYSTEM = ("You are the CRITIC in a Tree-of-Thought search. Judge each candidate analysis plan strictly. "
                 "Reply with a JSON object only.")

DEPTH_TASK = {
    1: ('Propose up to {k} distinct ways to MEASURE what the question asks. Each candidate: '
        '{{"question_type": "pre_post"|"spending_change"|"period_comparison", "state": code, "drug_class": code, '
        '"metric": one of {metrics}, "denominator_rationale": str, "assumptions": [str]}}.'),
    2: ('Refine the PARENT plan into up to {k} distinct COMPARISON DESIGNS. Keep all parent fields and add: '
        '"effective_date": "YYYY-MM-DD" or null (null only if no policy is involved), "date_source": document id '
        'or "unknown", "comparison": {{"type": "pre_post"|"two_quarters", "window_quarters": int, '
        '"start_quarter": "YYYYQn" or null, "end_quarter": "YYYYQn" or null}}, "checks": [tool names].'),
    3: ('Turn the PARENT plan into up to {k} distinct EXECUTABLE plans. Keep all parent fields and add: "steps": '
        'ordered [{{"tool": name, "args": {{...}}}}] using ONLY these tools: {tools}; and '
        '"interpretation_limits": [str] (what the result can and cannot show).'),
}
TOOL_SIGS = ("get_policy_timeline(state, topic), check_data_coverage(state), pre_post_analysis(state, drug_class, "
             "effective_date, metric, materiality_pct), spending_decomposition(state, drug_class, start_quarter, "
             "end_quarter), run_sql(sql), search_policy(query, state), calculate(expression)")


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        return json.loads(m.group(0) if m else text)
    except (json.JSONDecodeError, AttributeError):
        return {}


def _q(s: str | None) -> tuple[int, int] | None:
    m = re.fullmatch(r"(\d{4})\s*Q([1-4])", (s or "").upper().strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


class ToTPlanner:
    def __init__(self, llm, toolbox: Toolbox, beam: int = 2, branching: int = 3, depth: int = 3,
                 prune_below: float = 7.0, budget: Budget | None = None, persist: bool = True):
        self.llm, self.tb = llm, toolbox
        self.beam, self.k, self.max_depth, self.prune_below = beam, branching, depth, prune_below
        self.budget = budget or Budget()
        self.persist = persist

    # ------------------------------------------------------------------ public
    def plan(self, question: str) -> dict:
        self.t0, self.model_calls, self.tool_calls, self.retries = time.perf_counter(), 0, 0, 0
        self.errors: list[str] = []
        self.facts: dict[str, dict] = {}            # verified facts gathered by tool checks
        self.scratch = WorkingMemory(f"planner:{question}")
        self.nodes: list[PlanNode] = []
        self.question = question
        root = PlanNode("root", None, 0, {"question": question}, status="kept")
        self.nodes.append(root)
        frontier, status = [root], "selected"

        for d in range(1, self.max_depth + 1):
            children: list[PlanNode] = []
            for parent in frontier:
                if self._out_of_budget():
                    break
                children += self._expand(parent, d)
            if not children:
                status = "budget_exhausted" if self._out_of_budget() else "no_candidates"
                break
            self._score(children, d)
            self._targeted_evidence_checks(children, d)
            if d == 1:
                # Ground BEFORE the next generation step (Checkpoint 4.1 workflow: evidence first, then ToT).
                # Live finding: without this, depth-2 proposals guessed effective dates and the whole beam
                # was pruned. The evaluator can only reject; it cannot create a good branch.
                self._ground([c for c in children if not c.hard_failures])
            survivors = [c for c in children if not c.hard_failures and c.score >= self.prune_below]
            for c in children:
                if c not in survivors:
                    c.status = "pruned"
                    c.prune_reason = ("hard failure: " + "; ".join(c.hard_failures)) if c.hard_failures \
                        else f"score {c.score:.1f} < {self.prune_below}"
            survivors.sort(key=self._rank, reverse=True)
            for c in survivors[self.beam:]:
                c.status, c.prune_reason = "pruned", f"outside beam (width {self.beam})"
            frontier = survivors[:self.beam]
            for c in frontier:
                c.status = "kept"
            if not frontier:
                status = "no_valid_plan"
                break
            if self._out_of_budget() and d < self.max_depth:
                status = "budget_exhausted"
                break

        complete = [n for n in self.nodes if n.depth == self.max_depth and n.status == "kept"]
        best = max(complete, key=self._rank) if complete else None
        if best:
            best.status, status = "selected", "selected"
        return self._result(status, best)

    # ------------------------------------------------------------------ generation
    def _expand(self, parent: PlanNode, d: int) -> list[PlanNode]:
        task = DEPTH_TASK[d].format(k=self.k, metrics=sorted(METRICS), tools=TOOL_SIGS)
        ctx = {"question": self.question,
               "data": {"states": {"NL": "Northland", "EV": "Eastvale", "SP": "Southport",
                                   "IL": "Illinois (real data)"},
                        "drug_classes": sorted(DRUGS), "quarters": "2023Q1-2025Q4 (IL: 2022Q1-2026Q1)",
                        "policy_topics": ["NL:glp1_coverage", "EV:glp1_prior_auth", "SP:sglt2_pdl",
                                          "IL:glp1_coverage"]},
               "verified_facts": self.facts or "none yet",
               "parent": parent.plan if d > 1 else None}
        content = self._llm([{"role": "system", "content": GEN_SYSTEM},
                             {"role": "user", "content": json.dumps(ctx, default=str) + "\n\nTASK: " + task +
                              ' Return JSON: {"candidates": [...]}'}], max_tokens=4000)
        cands = _parse_json(content).get("candidates", [])
        out = []
        for i, c in enumerate(cands[: self.k] if isinstance(cands, list) else []):
            if not isinstance(c, dict):
                continue
            plan = {**(parent.plan if d > 1 else {}), **c}
            plan.pop("question", None)
            node = PlanNode(f"{parent.id if d > 1 else 'n'}{'.' if d > 1 else ''}{i + 1}", parent.id, d, plan)
            self.nodes.append(node)
            out.append(node)
        return out

    # ------------------------------------------------------------------ evaluation
    def _score(self, nodes: list[PlanNode], d: int) -> None:
        for n in nodes:
            self._deterministic(n, d)
        crit = self._critic([n for n in nodes if not n.hard_failures])
        for n in nodes:
            c = crit.get(n.id, {})
            n.breakdown["interpretation"] = max(0, min(2, int(c.get("interpretation", 1))))
            n.critic_note = str(c.get("note", ""))[:240]
            self._total(n, d)

    def _total(self, n: PlanNode, d: int) -> None:
        maxes = {"scope": 3, "interpretation": 2}
        if d >= 2:
            maxes.update(evidence=3, feasibility=1 if d == 2 else 2)
        earned = sum(n.breakdown.get(k, 0) for k in maxes)
        n.score = round(earned / sum(maxes.values()) * 10, 2)

    def _deterministic(self, n: PlanNode, d: int) -> None:
        p, b, hf = n.plan, n.breakdown, n.hard_failures
        state, drug, metric = str(p.get("state", "")).upper(), str(p.get("drug_class", "")).upper(), p.get("metric")
        qtype = p.get("question_type")
        if state not in STATES:
            hf.append(f"state '{state}' has no data")
        if drug not in DRUGS:
            hf.append(f"drug class '{drug}' not in data")
        if metric not in METRICS:
            hf.append(f"metric '{metric}' unavailable")
        spend_q = bool(re.search(r"spend|cost|reimburs|dollar|\$", self.question, re.I))
        fits = (metric in SPEND_METRICS) if (spend_q or qtype == "spending_change") else metric in METRICS
        b["scope"] = (state in STATES) + (drug in DRUGS) + bool(fits and metric in METRICS)
        if d < 2 or hf:
            return

        # evidence: effective date must match the VERIFIED policy timeline (gathered via tool calls)
        eff = p.get("effective_date")
        topic_fact = self.facts.get(f"timeline:{state}:{drug}")
        if eff:
            if topic_fact is None:
                b["evidence"], n.needs_evidence = 0, True
            elif topic_fact.get("status") == "unknown":
                b["evidence"] = 0
                hf.append(f"no policy on record for {state}/{drug}; date {eff} is unverified")
            else:
                superseded = {s.get("value") for s in topic_fact.get("superseded", [])}
                conflicts = {c.get("value") for c in topic_fact.get("conflicts", [])}
                if eff == topic_fact.get("value"):
                    b["evidence"] = 2 + (str(p.get("date_source", "")) in
                                         {topic_fact.get("source_doc"), *topic_fact.get("all_sources", [])})
                elif eff in superseded:
                    b["evidence"] = 0
                    hf.append(f"uses superseded date {eff} (authoritative: {topic_fact.get('value')})")
                elif eff in conflicts:
                    b["evidence"] = 0
                    hf.append(f"uses lower-authority date {eff} (authoritative: {topic_fact.get('value')})")
                else:
                    b["evidence"] = 0
        else:
            # No date: fine for a pure period comparison; a plan that drops the date for a POLICY question
            # gets no evidence credit (it would ignore the very event the user asked about).
            b["evidence"] = 0 if POLICY_WORDS.search(self.question) else 3

        # feasibility: enough FINAL quarters in the windows (needs coverage, gathered via tool call)
        cov = self.facts.get(f"coverage:{state}")
        if cov is None:
            b["feasibility"], n.needs_evidence = 0, True
        else:
            final = {_q(x) for x in cov.get("final", [])}
            ok, why = self._windows_ok(p, final)
            b["feasibility"] = 1 if ok else 0
            if not ok and why:
                hf.append(why)
        if d >= 3:
            steps = p.get("steps") or []
            bad = [s for s in steps if not isinstance(s, dict) or s.get("tool") not in STEP_TOOLS
                   or any(a not in (s.get("args") or {}) for a in REQUIRED_ARGS.get(s.get("tool"), []))]
            analytic = any(isinstance(s, dict) and s.get("tool") in
                           {"pre_post_analysis", "spending_decomposition", "run_sql"} for s in steps)
            if bad:
                hf.append(f"invalid step(s): {[s.get('tool') if isinstance(s, dict) else s for s in bad][:3]}")
            b["feasibility"] += 1 if (steps and analytic and not bad) else 0

    def _windows_ok(self, p: dict, final: set) -> tuple[bool, str]:
        comp = p.get("comparison") or {}
        if comp.get("type") == "two_quarters" or p.get("question_type") == "period_comparison":
            a, z = _q(comp.get("start_quarter")), _q(comp.get("end_quarter"))
            if not (a and z):
                return False, ""
            missing = [f"{y}Q{q}" for y, q in (a, z) if (y, q) not in final]
            if missing:
                return False, f"compares non-final quarter(s) {missing} (provisional or missing)"
            if p.get("effective_date") and POLICY_WORDS.search(self.question):
                # Weak, not invalid: one quarter per side gives no uncertainty estimate for a policy effect.
                return False, ""
            return True, ""
        try:
            eff = date.fromisoformat(str(p.get("effective_date")))
        except ValueError:
            return False, ""
        w = int(comp.get("window_quarters") or 4)
        post, _ = stats.first_post_quarter(eff)
        pre = [stats._shift(post, -i) for i in range(w, 0, -1)]
        if post != stats._quarter_of(eff):
            pre = [stats._shift(x, -1) for x in pre]
        postq = [stats._shift(post, i) for i in range(w)]
        n_pre, n_post = sum(x in final for x in pre), sum(x in final for x in postq)
        if n_pre < 2 or n_post < 2:
            return False, f"fewer than 2 final quarters before ({n_pre}) or after ({n_post}) the date"
        return True, ""

    def _critic(self, nodes: list[PlanNode]) -> dict:
        if not nodes or self._out_of_budget():
            return {}
        payload = {"question": self.question, "verified_facts": self.facts or "none yet",
                   "candidates": [{"id": n.id, "plan": n.plan} for n in nodes]}
        task = ('Score each candidate. "interpretation" 0-2: 2 = its metric and design actually answer the question, '
                'and it avoids claiming causation and discloses data limits; 1 = partly; 0 = would mislead '
                '(e.g. raw counts when enrollment changed, or spending treated as care). Add a short "note". '
                'Return JSON: {"scores": [{"id": str, "interpretation": int, "note": str}]}')
        content = self._llm([{"role": "system", "content": CRITIC_SYSTEM},
                             {"role": "user", "content": json.dumps(payload, default=str) + "\n\nTASK: " + task}],
                            max_tokens=2000)
        return {s.get("id"): s for s in _parse_json(content).get("scores", []) if isinstance(s, dict)}

    def _targeted_evidence_checks(self, nodes: list[PlanNode], d: int) -> None:
        """Premature-pruning mitigation: one targeted tool check per missing fact, then rescore."""
        needed = {(str(n.plan.get("state", "")).upper(), str(n.plan.get("drug_class", "")).upper())
                  for n in nodes if n.needs_evidence and not n.hard_failures}
        fetched = False
        for state, drug in sorted(needed):
            if f"coverage:{state}" not in self.facts and self._tool_ok():
                obs = self._tool("check_data_coverage", {"state": state})
                self.facts[f"coverage:{state}"] = obs.get("quarters_by_status", {})
                fetched = True
            if f"timeline:{state}:{drug}" not in self.facts and self._tool_ok():
                self.facts[f"timeline:{state}:{drug}"] = self._timeline(state, drug)
                fetched = True
        if fetched:
            for n in nodes:
                if n.needs_evidence and not n.hard_failures:
                    n.needs_evidence = False
                    for k in ("evidence", "feasibility"):
                        n.breakdown.pop(k, None)
                    self._deterministic(n, d)
                    self._total(n, d)

    def _ground(self, nodes: list[PlanNode]) -> None:
        """Fetch the policy timeline and data coverage for each (state, drug) still in play (tool calls)."""
        for state, drug in sorted({(str(n.plan.get("state", "")).upper(), str(n.plan.get("drug_class", "")).upper())
                                   for n in nodes}):
            if state not in STATES:
                continue
            if f"coverage:{state}" not in self.facts and self._tool_ok():
                self.facts[f"coverage:{state}"] = self._tool("check_data_coverage", {"state": state}).get(
                    "quarters_by_status", {})
            if f"timeline:{state}:{drug}" not in self.facts and self._tool_ok():
                self.facts[f"timeline:{state}:{drug}"] = self._timeline(state, drug)

    def _timeline(self, state: str, drug: str) -> dict:
        topics = sorted({f.key.split(":")[1] for f in self.tb.knowledge.facts
                         if f.key.startswith(f"{state}:") and drug.lower() in f.key.split(":")[1]})
        if not topics:
            return {"status": "unknown"}
        obs = self._tool("get_policy_timeline", {"state": state, "topic": topics[0]})
        if "error" in obs:
            return {"status": "unknown"}
        obs["all_sources"] = [obs.get("source_doc")]
        return obs

    # ------------------------------------------------------------------ plumbing
    def _rank(self, n: PlanNode):
        steps = len(n.plan.get("steps") or [])
        return (n.score, n.breakdown.get("evidence", 0), n.breakdown.get("scope", 0), -steps)

    def _llm(self, messages: list[dict], max_tokens: int) -> str:
        """One planner model call; an empty or unparseable reply (seen live: the output budget used up before
        any JSON) gets ONE retry with a brevity nudge. Both attempts count toward the budget."""
        self.model_calls += 1
        content = self._call(messages, max_tokens)
        if _parse_json(content) or self._out_of_budget():
            return content
        self.model_calls += 1
        self.retries = getattr(self, "retries", 0) + 1
        nudge = {"role": "user", "content": "Your previous reply was empty or not valid JSON. Reply again with "
                                            "ONLY the compact JSON object, very short strings."}
        return self._call(messages + [nudge], max_tokens)

    def _call(self, messages: list[dict], max_tokens: int) -> str:
        """Model call with a hard per-call timeout derived from the remaining time budget. A network failure
        becomes an empty reply (the search degrades) instead of hanging (live: a 49-minute stall)."""
        remaining = max(10.0, self.budget.seconds - (time.perf_counter() - self.t0) + 20.0)
        try:
            return self.llm.chat(messages, tools=None, max_tokens=max_tokens, json_mode=True,
                                 timeout=remaining).content
        except TypeError:   # scripted/test LLMs without a timeout parameter
            return self.llm.chat(messages, tools=None, max_tokens=max_tokens, json_mode=True).content
        except Exception as exc:  # noqa: BLE001 - provider/network errors must not crash or hang planning
            self.errors = getattr(self, "errors", []) + [f"{type(exc).__name__}: {str(exc)[:120]}"]
            return ""

    def _tool(self, name: str, args: dict) -> dict:
        self.tool_calls += 1
        return self.tb.execute(name, json.dumps(args), self.scratch)

    def _tool_ok(self) -> bool:
        return self.tool_calls < self.budget.tool_calls and not self._timed_out()

    def _timed_out(self) -> bool:
        return time.perf_counter() - self.t0 > self.budget.seconds

    def _out_of_budget(self) -> bool:
        return self.model_calls >= self.budget.model_calls or self._timed_out()

    def _result(self, status: str, best: PlanNode | None) -> dict:
        plan_id = f"plan_{uuid.uuid4().hex[:8]}"
        used = {"model_calls": self.model_calls, "tool_calls": self.tool_calls,
                "seconds": round(time.perf_counter() - self.t0, 1), "retries": getattr(self, "retries", 0),
                "errors": getattr(self, "errors", [])}
        tree = [asdict(n) for n in self.nodes]
        if self.persist:
            PLANS_DIR.mkdir(parents=True, exist_ok=True)
            (PLANS_DIR / f"{plan_id}.json").write_text(json.dumps(
                {"plan_id": plan_id, "question": self.question, "status": status, "budget_used": used,
                 "verified_facts": self.facts, "nodes": tree}, indent=2, default=str), encoding="utf-8")
        pruned = [{"id": n.id, "depth": n.depth, "reason": n.prune_reason,
                   "summary": {k: n.plan.get(k) for k in ("metric", "effective_date", "comparison") if k in n.plan}}
                  for n in self.nodes if n.status == "pruned"]
        out = {"plan_id": plan_id, "status": status, "budget_used": used,
               "nodes_explored": len(self.nodes) - 1, "pruned": pruned[:8], "verified_facts": self.facts}
        if best:
            out.update(selected_plan=best.plan, score=best.score, breakdown=best.breakdown,
                       critic_note=best.critic_note,
                       instruction="Execute selected_plan.steps with the tools, then answer.")
        else:
            out["instruction"] = ("No plan passed the checks. Do not run an analysis that the checks rejected; "
                                  "explain the reasons in 'pruned' (cannot_answer) or ask the user to clarify.")
        return out
