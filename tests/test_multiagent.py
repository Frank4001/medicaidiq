"""Multi-agent workflow (Week 5): roles, tool isolation, shared evidence, review loop, failover."""

import json

from medicaidiq.llm import AssistantTurn, ScriptedLLM, tool_turn
from medicaidiq.multiagent import DATA_TOOLS, POLICY_TOOLS, MultiAgentOrchestrator

Q = "Did GLP-1 prescribing in Eastvale change after its prior authorization requirement took effect?"


def j(obj) -> AssistantTurn:
    return AssistantTurn(json.dumps(obj), [])


def ans(summary, findings, answer_type="answer"):
    return {"answer_type": answer_type, "summary": summary, "findings": findings, "confidence": "high",
            "confidence_reason": "r", "caveats": ["synthetic data; association only"]}


SCOPE = j({"states": ["EV"], "drug_class": "GLP1", "needs_policy": True, "needs_data": True,
           "clarification": None, "notes": "pre/post"})
RESEARCH = [tool_turn("look up date", "get_policy_timeline", {"state": "EV", "topic": "glp1_prior_auth"}, "r1"),
            tool_turn("handoff", "submit_answer", ans(
                "Effective 2024-07-01 per EV-MAN-2024-PH; the 2024-06-01 bulletin date was superseded.",
                [{"statement": "Authoritative date 2024-07-01", "evidence_ids": ["E1"]}]), "r2")]
ANALYSIS = [tool_turn("run pre/post", "pre_post_analysis",
                      {"state": "EV", "drug_class": "GLP1", "effective_date": "2024-07-01"}, "a1"),
            tool_turn("handoff", "submit_answer", ans(
                "GLP-1 Rx per 1,000 MM fell 18.6% (CI -20.8 to -16.2), significant and material.",
                [{"statement": "-18.6%", "evidence_ids": ["E3"]}]), "a2")]
FINAL = tool_turn("write", "submit_answer", ans(
    "GLP-1 prescribing fell 18.6% after the July 1, 2024 prior authorization (association, not causation).",
    [{"statement": "-18.6% (95% CI -20.8% to -16.2%)", "evidence_ids": ["E1", "E2", "E3"]}]), "w1")
APPROVE = j({"verdict": "approve", "issues": []})


def test_full_workflow_roles_and_shared_evidence(toolbox):
    llm = ScriptedLLM([SCOPE, *RESEARCH, *ANALYSIS, FINAL, APPROVE])
    res = MultiAgentOrchestrator(llm, toolbox).run(Q)
    assert res.stop_reason == "submitted" and res.verified, res.answer
    actions = [t.action for t in res.trace]
    for needed in ("planner_scope", "get_policy_timeline", "controller_prefetch", "pre_post_analysis", "reviewer"):
        assert needed in actions
    roles = {t.thought.split("]")[0] + "]" for t in res.trace if t.thought.startswith("[")}
    assert {"[planner]", "[researcher]", "[analyst]", "[reviewer]"} <= roles
    tools = {e["tool"] for e in res.evidence}                 # one shared store across agents
    assert {"get_policy_timeline", "check_data_coverage", "pre_post_analysis"} <= tools
    assert res.usage["workflow"] == "multi-agent"


def test_sub_agents_only_see_their_own_tools(toolbox):
    from medicaidiq.agent import MedicaidIQAgent
    r = MedicaidIQAgent(ScriptedLLM([]), toolbox, allowed_tools=POLICY_TOOLS)
    a = MedicaidIQAgent(ScriptedLLM([]), toolbox, allowed_tools=DATA_TOOLS)
    assert r.offered == POLICY_TOOLS | {"submit_answer"}
    assert a.offered == DATA_TOOLS | {"submit_answer"}
    assert not (r.offered & DATA_TOOLS) and not (a.offered & POLICY_TOOLS)


def test_reviewer_triggers_one_targeted_revision(toolbox):
    revise = j({"verdict": "revise", "issues": [{"severity": "material", "issue": "claims causation",
                                                 "fix": "say association"}]})
    llm = ScriptedLLM([SCOPE, *RESEARCH, *ANALYSIS, FINAL, revise, FINAL, APPROVE])
    res = MultiAgentOrchestrator(llm, toolbox).run(Q)
    assert sum(1 for t in res.trace if t.action == "reviewer") == 2
    assert any(t.thought.startswith("[planner-revision]") for t in res.trace)


def test_unresolved_review_is_disclosed_not_looped(toolbox):
    revise = j({"verdict": "revise", "issues": [{"severity": "material", "issue": "window includes provisional data",
                                                 "fix": "exclude"}]})
    llm = ScriptedLLM([SCOPE, *RESEARCH, *ANALYSIS, FINAL, revise, FINAL, revise])
    res = MultiAgentOrchestrator(llm, toolbox, max_revisions=1).run(Q)
    assert any("Reviewer concern not resolved" in c for c in res.answer["caveats"])


def test_controller_failure_fails_over_to_single_agent(toolbox):
    class Broken(ScriptedLLM):
        def chat(self, *a, **k):
            raise ConnectionError("provider down")
    res = MultiAgentOrchestrator(Broken([]), toolbox).run(Q)
    assert res.trace[0].action == "controller_failover"
    assert res.stop_reason.startswith("failover:") and res.answer["answer_type"] == "cannot_answer"


def test_scope_clarification(toolbox):
    llm = ScriptedLLM([j({"states": [], "drug_class": None, "needs_policy": True, "needs_data": True,
                          "clarification": "Which state and policy do you mean?"})])
    res = MultiAgentOrchestrator(llm, toolbox).run("Did spending go up after the policy?")
    assert res.answer["answer_type"] == "clarification_needed"


def test_unknown_state_skips_analyst_and_abstains(toolbox):
    llm = ScriptedLLM([
        j({"states": ["Westmoor"], "drug_class": "GLP1", "needs_policy": False, "needs_data": True,
           "clarification": None}),
        tool_turn("w", "submit_answer", ans("Westmoor is not in the data, so this cannot be answered.", [],
                                            "cannot_answer")),
        APPROVE,
    ])
    res = MultiAgentOrchestrator(llm, toolbox).run("Did GLP-1 use change in Westmoor?")
    assert res.answer["answer_type"] == "cannot_answer"
    assert not any(t.thought.startswith("[analyst]") for t in res.trace)


def test_write_guardrail_still_applies(toolbox):
    llm = ScriptedLLM([])
    res = MultiAgentOrchestrator(llm, toolbox).run("Delete all rows from the enrollment table")
    assert res.stop_reason == "guardrail" and llm.seen_messages == []


def test_state_names_and_codes_normalize():
    from medicaidiq.multiagent import normalize_state
    assert [normalize_state(s) for s in ["Northland", "EV", "sp", "Southport (SP)", "Illinois"]] == \
        ["NL", "EV", "SP", "SP", "Illinois"]


def test_bare_cfr_mention_is_not_a_numeric_claim():
    from medicaidiq.verify import _claim_numbers
    assert _claim_numbers("Only federal 42 CFR passages were returned; none are Illinois-specific.") == []
