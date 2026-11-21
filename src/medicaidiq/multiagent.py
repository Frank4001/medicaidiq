"""Multi-agent workflow (Week 5; Checkpoint 5.1 design, revised per grader feedback).

Four LLM agents plus a deterministic controller:

  Controller (code, no LLM)  routes stages, runs the parallel phase, enforces the revision cap and
                             time limits, and FAILS OVER to the single agent if any stage breaks
  Planner                    scopes the question (states, drug class, which specialists are needed),
                             then writes the final answer from the team's evidence
  Policy researcher          sub-agent with policy tools only: search_policy, get_policy_timeline
  Claims analyst             sub-agent with data tools only: check_data_coverage, run_sql,
                             pre_post_analysis, spending_decomposition, calculate
  Reviewer                   checks the planner's DRAFT ANSWER only (plan scoring stays with the ToT
                             critic, so the reviewer has one job); at most one targeted revision

Coordination (hybrid, like a small graph):
  scope -> [policy researcher || coverage prefetch] -> claims analyst (waits for the policy
  findings: effective dates) -> planner drafts -> reviewer -> (<=1 revision) -> answer.
  Policy-only questions skip the analyst; data-only questions skip the researcher.

Communication: one shared evidence store (WorkingMemory = blackboard). Every tool result from
any agent gets a global id (E1, E2, ...) that the final answer must cite and the verifier checks.
Handoffs between agents are structured summaries (one-way); reviewer -> planner is the single
two-way loop.
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

from .agent import AgentResult, MedicaidIQAgent, TraceStep, write_intent
from .db import SCHEMA_DOC
from .guardrails import screen_input
from .memory import WorkingMemory
from .tools import Toolbox
from .verify import verify_answer

POLICY_TOOLS = {"search_policy", "get_policy_timeline"}
DATA_TOOLS = {"check_data_coverage", "run_sql", "pre_post_analysis", "spending_decomposition", "calculate"}
KNOWN_STATES = {"NL", "EV", "SP"}
_STATE_NAMES = {"NORTHLAND": "NL", "EASTVALE": "EV", "SOUTHPORT": "SP"}


def normalize_state(value) -> str:
    """Map a scoped state (code OR name) to its code; keep unknown states as given.
    Live bug (multi-agent v2): names were truncated to two letters ("Northland" -> "NO") and
    treated as unknown states, so the analyst was skipped."""
    raw = str(value).strip()
    for word in re.findall(r"[A-Z]+", raw.upper()):
        if word in KNOWN_STATES:
            return word
        if word in _STATE_NAMES:
            return _STATE_NAMES[word]
    return raw

_SHARED_RULES = """Rules: use tools for every fact and number; never compute in your head. Cite tool evidence ids
(E1, E2, ...) and, for policy text, chunk_ids in brackets. Data and states are synthetic (NL Northland,
EV Eastvale, SP Southport). Policy topics: glp1_coverage (NL), glp1_prior_auth (EV), sglt2_pdl (SP).
Finish by calling submit_answer: summary = your handoff note for the team (what you found, what is
uncertain); findings = the facts with evidence ids. If you found nothing usable, use answer_type
"cannot_answer" and say exactly what is missing."""

RESEARCHER_PROMPT = f"""You are the POLICY RESEARCHER on the MedicaidIQ team. Your only job is policy evidence:
authoritative effective dates (get_policy_timeline: report superseded and conflicting versions too) and
what documents say (search_policy: one search per state; federal passages never establish a state's rules;
respect low_relevance / out_of_scope flags). Do not analyze data.
{_SHARED_RULES}"""

ANALYST_PROMPT = f"""You are the CLAIMS ANALYST on the MedicaidIQ team. Your only job is the data. Use the policy
researcher's effective dates exactly as given (never an announcement or superseded date). Prefer per-member
rates over raw counts; pre_post_analysis for before/after; spending_decomposition whenever spending is involved;
exclude and disclose provisional or missing quarters. Report effect size, CI, and verdict.
{_SHARED_RULES}

{SCHEMA_DOC}"""

WRITER_PROMPT = """You are the PLANNER on the MedicaidIQ team, now writing the FINAL answer for the user from your
team's handoffs and the shared evidence list. Do not introduce any number or fact that is not in the evidence.
Cite evidence ids in every finding and chunk_ids for policy text. State association, not causation, for
pre/post results; disclose superseded or conflicting sources, excluded quarters, and synthetic data. Never make
policy recommendations. If the team could not establish the answer, use answer_type "cannot_answer" and say
what is missing. When declining, do NOT add facts about other states (never place one state's rules next to a
question about another). Copy document and chunk ids EXACTLY as given (e.g. NL-SPA-2023-014#p1). Keep it concise (summary <= 4 sentences, <= 6 findings). Call submit_answer."""

SCOPE_PROMPT = """You are the PLANNER on the MedicaidIQ team. Scope the user's question. Available data: states
NL (Northland), EV (Eastvale), SP (Southport); drug classes GLP1, SGLT2, INSULIN; quarters 2023Q1-2025Q4; policy
documents for those states plus federal regulations. Reply with JSON only:
{"states": [state codes or names as asked; include unknown states as named], "drug_class": code or null,
 "needs_policy": true if any policy date or policy text matters, "needs_data": true if utilization or spending
 numbers are needed, "clarification": a single question if an essential detail (e.g. which state) is missing
 and cannot be inferred, else null, "notes": "one sentence"}"""

REVIEW_PROMPT = """You are the REVIEWER on the MedicaidIQ team. Check the DRAFT answer against the question and the
evidence summaries. Flag only MATERIAL problems that could change what the user believes: wrong jurisdiction;
superseded or lower-authority date used; provisional or missing quarters treated as real; raw counts or spending
presented as changes in care; causal claims from pre/post data; claims not supported by the evidence; an
undisclosed source conflict; answering when it should abstain (or vice versa). Reply with JSON only:
{"verdict": "approve" | "revise", "issues": [{"severity": "material" | "minor", "issue": "...", "fix": "..."}]}"""


def _json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    try:
        return json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return {}


class MultiAgentOrchestrator:
    def __init__(self, llm, toolbox: Toolbox, max_revisions: int = 1, max_seconds: float = 300.0,
                 fallback: bool = True):
        self.llm, self.tb = llm, toolbox
        self.max_revisions, self.max_seconds, self.fallback = max_revisions, max_seconds, fallback

    # ------------------------------------------------------------------ public
    def run(self, question: str) -> AgentResult:
        t0 = time.perf_counter()
        if write_intent(question) or not screen_input(question).allowed:   # same deterministic guardrails
            single = MedicaidIQAgent(self.llm, self.tb)
            res = single.run(question)
            self.last_memory = single.last_memory
            return res
        try:
            return self._run(question, t0)
        except Exception as exc:  # noqa: BLE001 - CONTROLLER FAILOVER (grader feedback, Checkpoint 5.1)
            if not self.fallback:
                raise
            single = MedicaidIQAgent(self.llm, self.tb)
            res = single.run(question)
            self.last_memory = single.last_memory
            res.trace.insert(0, TraceStep(0, f"[controller] multi-agent workflow failed "
                                             f"({type(exc).__name__}: {str(exc)[:120]}); failing over to single agent",
                                          "controller_failover", {}, {"error": str(exc)[:300]}, 0.0))
            res.stop_reason = f"failover:{res.stop_reason}"
            return res

    # ------------------------------------------------------------------ workflow
    def _run(self, question: str, t0: float) -> AgentResult:
        memory = WorkingMemory(question)          # shared blackboard for every agent
        self.last_memory = memory
        trace: list[TraceStep] = []
        stage = lambda name, note, obs=None: trace.append(TraceStep(len(trace) + 1, note, name, {}, obs or {}, 0.0))

        # 1. Planner scopes the question
        scope = _json(self.llm.chat([{"role": "system", "content": SCOPE_PROMPT},
                                     {"role": "user", "content": question}],
                                    max_tokens=600, json_mode=True, timeout=60).content) or \
            {"states": [], "needs_policy": True, "needs_data": True, "clarification": None}
        stage("planner_scope", f"[planner] scope: {json.dumps(scope)[:300]}", scope)
        if scope.get("clarification") and not scope.get("states"):
            answer = {"answer_type": "clarification_needed", "summary": scope["clarification"], "findings": [],
                      "confidence": "low", "confidence_reason": "Question is missing an essential detail.",
                      "caveats": [], "human_review": []}
            return self._result(question, answer, trace, memory, "clarification", True, t0)
        states = [normalize_state(s) for s in scope.get("states", [])]
        unknown = [s for s in states if s not in KNOWN_STATES]

        # 2. Parallel phase: policy researcher || deterministic coverage prefetch for the analyst
        researcher_res, coverage = None, {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_r = pool.submit(self._researcher, question, scope, memory) if scope.get("needs_policy") else None
            if scope.get("needs_data"):
                for s in states:
                    if s in KNOWN_STATES:
                        coverage[s] = self.tb.execute("check_data_coverage", json.dumps({"state": s}), memory)
            researcher_res = fut_r.result(timeout=self._remaining(t0)) if fut_r else None
        if researcher_res:
            trace += self._tag(researcher_res.trace, "researcher")
        if coverage:
            stage("controller_prefetch", "[controller] prefetched data coverage for the analyst",
                  {s: c.get("evidence_id") for s, c in coverage.items()})

        # 3. Claims analyst (waits for the policy findings)
        analyst_res = None
        if scope.get("needs_data") and not (unknown and not any(s in KNOWN_STATES for s in states)):
            analyst_res = self._analyst(question, scope, researcher_res, coverage, memory, t0)
            trace += self._tag(analyst_res.trace, "analyst")

        # 4. Planner drafts the final answer, 5. reviewer checks it (<= max_revisions targeted revisions)
        brief = self._brief(question, scope, researcher_res, analyst_res, memory, unknown)
        draft_res = self._write(brief, question, memory, t0)
        trace += self._tag(draft_res.trace, "planner")
        answer, verified = draft_res.answer, draft_res.verified
        for revision in range(self.max_revisions + 1):
            review = self._review(question, answer, memory)
            material = [i for i in review.get("issues", []) if i.get("severity") == "material"]
            stage("reviewer", f"[reviewer] verdict={review.get('verdict')} material_issues={len(material)}", review)
            if review.get("verdict") != "revise" or not material:
                break
            if revision == self.max_revisions:   # revision budget spent: disclose instead of looping
                answer.setdefault("caveats", []).extend(
                    f"Reviewer concern not resolved: {i.get('issue', '')}" for i in material[:3])
                break
            fix = brief + "\n\nYOUR PREVIOUS DRAFT:\n" + json.dumps(answer)[:3000] + \
                "\n\nREVIEWER ISSUES TO FIX (targeted revision):\n" + json.dumps(material)[:1500]
            draft_res = self._write(fix, question, memory, t0)
            trace += self._tag(draft_res.trace, "planner-revision")
            answer, verified = draft_res.answer, draft_res.verified
        stop = "submitted" if answer.get("answer_type") != "clarification_needed" else "clarification"
        return self._result(question, answer, trace, memory, stop, verified and not verify_answer(answer, memory), t0)

    # ------------------------------------------------------------------ agents
    def _researcher(self, question, scope, memory):
        task = (f"Team question: {question}\nScope from planner: {json.dumps(scope)}\n"
                "Gather the policy evidence the team needs.")
        return MedicaidIQAgent(self.llm, self.tb, max_steps=8, system_prompt=RESEARCHER_PROMPT,
                               allowed_tools=POLICY_TOOLS, max_seconds=120).run(task, memory)

    def _analyst(self, question, scope, researcher_res, coverage, memory, t0):
        policy_note = (json.dumps({k: researcher_res.answer.get(k) for k in ("summary", "findings", "caveats")})[:2500]
                       if researcher_res else "No policy research was needed for this question.")
        cov_note = json.dumps({s: {"evidence_id": c.get("evidence_id"),
                                   "quarters_by_status": c.get("quarters_by_status")} for s, c in coverage.items()})
        task = (f"Team question: {question}\nScope from planner: {json.dumps(scope)}\n"
                f"Policy researcher handoff: {policy_note}\nData coverage already fetched: {cov_note}\n"
                "Run the analysis the question needs.")
        return MedicaidIQAgent(self.llm, self.tb, max_steps=8, system_prompt=ANALYST_PROMPT,
                               allowed_tools=DATA_TOOLS, max_seconds=min(150, self._remaining(t0))).run(task, memory)

    def _write(self, brief, question, memory, t0):
        return MedicaidIQAgent(self.llm, self.tb, max_steps=3, system_prompt=WRITER_PROMPT, allowed_tools=set(),
                               max_seconds=min(90, self._remaining(t0))).run(brief, memory)

    def _review(self, question, answer, memory):
        payload = {"question": question, "draft": answer,
                   "evidence": [{"id": e.id, "tool": e.kind, "summary": e.summary} for e in memory.evidence]}
        reply = self.llm.chat([{"role": "system", "content": REVIEW_PROMPT},
                               {"role": "user", "content": json.dumps(payload, default=str)[:9000]}],
                              max_tokens=1200, json_mode=True, timeout=60)
        return _json(reply.content) or {"verdict": "approve", "issues": [], "note": "review unparseable; skipped"}

    # ------------------------------------------------------------------ helpers
    def _brief(self, question, scope, researcher_res, analyst_res, memory, unknown) -> str:
        part = lambda r: json.dumps({k: r.answer.get(k) for k in ("answer_type", "summary", "findings", "caveats")})[:3000] \
            if r else "(not needed)"
        ev = "\n".join(f"{e.id} [{e.kind}] {e.summary}" for e in memory.evidence)
        # Lossless policy handoff (live failure: the writer saw only one-line summaries, not passage text).
        texts = [f"{e.id} {p['chunk_id']} ({p['scope']}): {p['text'][:450]}"
                 for e in memory.evidence if e.kind == "search_policy"
                 for p in e.payload.get("passages", [])[:4]]
        if texts:
            ev += "\nRETRIEVED POLICY TEXT (quote from here):\n" + "\n".join(texts)[:5000]
        return (f"USER QUESTION: {question}\nSCOPE: {json.dumps(scope)}\n"
                + (f"STATES WITH NO DATA OR DOCUMENTS: {unknown}\n" if unknown else "")
                + f"POLICY RESEARCHER HANDOFF: {part(researcher_res)}\nCLAIMS ANALYST HANDOFF: {part(analyst_res)}\n"
                f"SHARED EVIDENCE (cite these ids):\n{ev or '(none)'}")

    @staticmethod
    def _tag(steps: list[TraceStep], role: str) -> list[TraceStep]:
        return [TraceStep(s.step, f"[{role}] {s.thought}", s.action, s.arguments, s.observation, s.latency_s)
                for s in steps]

    def _remaining(self, t0: float) -> float:
        return max(5.0, self.max_seconds - (time.perf_counter() - t0))

    def _result(self, question, answer, trace, memory, stop, verified, t0) -> AgentResult:
        m = self.llm.meter
        usage = {"model": getattr(self.llm, "label", "?"), "llm_calls": m.calls, "prompt_tokens": m.prompt_tokens,
                 "cached_tokens": m.cached_tokens, "completion_tokens": m.completion_tokens,
                 "llm_seconds": round(m.seconds, 2), "cost_usd": m.cost_usd(self.llm.price),
                 "wall_seconds": round(time.perf_counter() - t0, 1), "workflow": "multi-agent"}
        evidence = [{"id": e.id, "tool": e.kind, "summary": e.summary} for e in memory.evidence]
        return AgentResult(question, answer, trace, evidence, stop, verified, len(trace), usage)
