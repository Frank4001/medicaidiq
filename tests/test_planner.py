"""Tree-of-Thought planner (Week 4): structure, deterministic checks, pruning, budget, agent integration."""

import json

from medicaidiq.agent import MedicaidIQAgent
from medicaidiq.llm import AssistantTurn, ScriptedLLM, tool_turn
from medicaidiq.planner import Budget, ToTPlanner

Q = "Did GLP-1 prescribing in Eastvale change after its prior authorization requirement took effect?"


def j(obj) -> AssistantTurn:
    return AssistantTurn(json.dumps(obj), [])


D1 = j({"candidates": [
    {"question_type": "pre_post", "state": "EV", "drug_class": "GLP1", "metric": "rx_per_1000_mm",
     "denominator_rationale": "per member to control for enrollment"},
    {"question_type": "pre_post", "state": "EV", "drug_class": "GLP1", "metric": "raw_prescription_count"},
    {"question_type": "pre_post", "state": "IL", "drug_class": "GLP1", "metric": "rx_per_1000_mm"},
]})
C1 = j({"scores": [{"id": "n1", "interpretation": 2, "note": "per-member rate answers the question"}]})
D2 = j({"candidates": [
    {"effective_date": "2024-07-01", "date_source": "EV-MAN-2024-PH",
     "comparison": {"type": "pre_post", "window_quarters": 4}},
    {"effective_date": "2024-06-01", "date_source": "EV-BUL-2024-007",
     "comparison": {"type": "pre_post", "window_quarters": 4}},
    {"effective_date": "2025-07-01", "date_source": "unknown",
     "comparison": {"type": "pre_post", "window_quarters": 4}},
]})
C2 = j({"scores": [{"id": f"n1.{i}", "interpretation": 2} for i in (1, 2, 3)]})
D3 = j({"candidates": [
    {"steps": [{"tool": "pre_post_analysis",
                "args": {"state": "EV", "drug_class": "GLP1", "effective_date": "2024-07-01"}}],
     "interpretation_limits": ["association, not causation"]},
    {"steps": [{"tool": "delete_rows", "args": {}}], "interpretation_limits": []},
]})
C3 = j({"scores": [{"id": "n1.1.1", "interpretation": 2}]})


def _planner(toolbox, turns, **kw):
    return ToTPlanner(ScriptedLLM(turns), toolbox, persist=False, **kw)


def test_full_tree_selects_correct_plan_and_prunes_traps(toolbox):
    res = _planner(toolbox, [D1, C1, D2, C2, D3, C3]).plan(Q)
    assert res["status"] == "selected"
    assert res["selected_plan"]["effective_date"] == "2024-07-01"
    assert res["score"] >= 7
    reasons = {p["id"]: p["reason"] for p in res["pruned"]}
    assert "metric" in reasons["n2"] and "state 'IL'" in reasons["n3"]
    assert "superseded date 2024-06-01" in reasons["n1.2"]
    assert "fewer than 2 final quarters" in reasons["n1.3"]       # provisional 2025Q3/Q4
    assert "invalid step" in reasons["n1.1.2"]
    assert res["budget_used"]["model_calls"] == 6                  # 3 generate + 3 critic
    assert res["budget_used"]["tool_calls"] == 2                   # targeted coverage + timeline checks
    assert res["nodes_explored"] <= 15


def test_targeted_evidence_check_runs_before_pruning(toolbox):
    res = _planner(toolbox, [D1, C1, D2, C2, D3, C3]).plan(Q)
    assert res["verified_facts"]["timeline:EV:GLP1"]["value"] == "2024-07-01"
    assert "2025Q4" in res["verified_facts"]["coverage:EV"]["provisional"]


def test_beam_width_limits_frontier(toolbox):
    d1 = j({"candidates": [{"question_type": "pre_post", "state": "EV", "drug_class": "GLP1",
                            "metric": m} for m in ("rx_per_1000_mm", "spend_pmpm", "spend_per_rx")]})
    c1 = j({"scores": [{"id": f"n{i}", "interpretation": 2} for i in (1, 2, 3)]})
    p = _planner(toolbox, [d1, c1], depth=1, beam=2)
    res = p.plan(Q)
    kept = [n for n in p.nodes if n.depth == 1 and n.status in ("kept", "selected")]
    assert len(kept) == 2 and any("outside beam" in x["reason"] for x in res["pruned"])


def test_no_valid_plan_when_everything_fails(toolbox):
    d1 = j({"candidates": [{"question_type": "pre_post", "state": "IL", "drug_class": "GLP1",
                            "metric": "rx_per_1000_mm"}]})
    res = _planner(toolbox, [d1]).plan("Did GLP-1 use change in Illinois after its PA policy?")
    assert res["status"] == "no_valid_plan" and "selected_plan" not in res
    assert "explain" in res["instruction"]


def test_budget_exhaustion_stops_search(toolbox):
    res = _planner(toolbox, [D1, C1, D2, C2, D3, C3], budget=Budget(model_calls=2)).plan(Q)
    assert res["status"] == "budget_exhausted" and res["budget_used"]["model_calls"] == 2


def test_period_comparison_with_provisional_quarter_is_rejected(toolbox):
    q = "Compare Eastvale GLP-1 prescriptions per 1,000 member-months in 2025Q2 versus 2025Q4."
    d1 = j({"candidates": [{"question_type": "period_comparison", "state": "EV", "drug_class": "GLP1",
                            "metric": "rx_per_1000_mm"}]})
    c1 = j({"scores": [{"id": "n1", "interpretation": 2}]})
    d2 = j({"candidates": [{"effective_date": None, "comparison": {"type": "two_quarters",
                                                                   "start_quarter": "2025Q2", "end_quarter": "2025Q4"}}]})
    c2 = j({"scores": [{"id": "n1.1", "interpretation": 2}]})
    res = _planner(toolbox, [d1, c1, d2, c2]).plan(q)
    assert res["status"] == "no_valid_plan"
    assert "non-final quarter" in res["pruned"][0]["reason"]


def test_empty_model_reply_is_retried_once(toolbox):
    """Regression (live DeepSeek): output budget used up before any JSON -> empty content."""
    empty = AssistantTurn("", [])
    res = _planner(toolbox, [D1, C1, empty, D2, C2, D3, C3]).plan(Q)
    assert res["status"] == "selected" and res["budget_used"]["retries"] == 1
    assert res["budget_used"]["model_calls"] == 7


def test_agent_offers_plan_tool_only_when_enabled(toolbox):
    assert "plan_analysis" not in MedicaidIQAgent(ScriptedLLM([]), toolbox).offered
    assert "plan_analysis" in MedicaidIQAgent(ScriptedLLM([]), toolbox, planning=True).offered


def test_agent_runs_planner_then_executes_plan(toolbox):
    planner = _planner(toolbox, [D1, C1, D2, C2, D3, C3])
    llm = ScriptedLLM([
        tool_turn("Thought: plan first.", "plan_analysis", {"question": Q}),
        tool_turn("Thought: execute step 1.", "pre_post_analysis",
                  {"state": "EV", "drug_class": "GLP1", "effective_date": "2024-07-01"}),
        tool_turn("t", "submit_answer", {
            "answer_type": "answer", "summary": "Prescribing fell 18.6% after PA (association only).",
            "findings": [{"statement": "-18.6%", "evidence_ids": ["E2"]}], "confidence": "high",
            "confidence_reason": "r", "caveats": ["synthetic"]}),
    ])
    res = MedicaidIQAgent(llm, toolbox, planning=True, planner=planner).run(Q)
    assert [t.action for t in res.trace] == ["plan_analysis", "pre_post_analysis", "submit_answer"]
    assert res.trace[0].observation["status"] == "selected" and res.verified
