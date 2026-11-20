"""The eval grader itself is tested with scripted agents (a passing and a failing run)."""

from medicaidiq.agent import MedicaidIQAgent
from medicaidiq.evaluation import grade, load_cases
from medicaidiq.llm import ScriptedLLM, tool_turn


def _case(cid):
    return next(c for c in load_cases() if c["id"] == cid)


def test_grader_passes_correct_behaviour(toolbox):
    llm = ScriptedLLM([
        tool_turn("t", "get_policy_timeline", {"state": "EV", "topic": "glp1_prior_auth"}),
        tool_turn("t", "pre_post_analysis", {"state": "EV", "drug_class": "GLP1", "effective_date": "2024-07-01"}),
        tool_turn("t", "submit_answer", {
            "answer_type": "answer", "summary": "Prescribing fell 18.6% after PA; this is an association, not proof.",
            "findings": [{"statement": "-18.6% change", "evidence_ids": ["E2"]}], "confidence": "high",
            "confidence_reason": "r", "caveats": ["synthetic"]}),
    ])
    res = MedicaidIQAgent(llm, toolbox).run("q")
    g = grade(_case("prepost_ev"), res, toolbox)
    assert g["passed"], g["checks"]


def test_grader_catches_superseded_date(toolbox):
    llm = ScriptedLLM([
        tool_turn("t", "pre_post_analysis", {"state": "EV", "drug_class": "GLP1", "effective_date": "2024-06-01"}),
        tool_turn("t", "submit_answer", {
            "answer_type": "answer", "summary": "Prescribing fell 17.9%, an association.",
            "findings": [{"statement": "-17.9%", "evidence_ids": ["E1"]}], "confidence": "high",
            "confidence_reason": "r", "caveats": []}),
    ])
    res = MedicaidIQAgent(llm, toolbox).run("q")
    g = grade(_case("prepost_ev"), res, toolbox)
    assert not g["passed"] and g["checks"]["effective_date"] is False


def test_all_cases_are_well_formed():
    cases = load_cases()
    assert len(cases) >= 9 and len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        assert c["expect_type"] and c["question"] and c["category"]
