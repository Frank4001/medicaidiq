"""ReAct loop behaviour with a scripted LLM (no network, deterministic)."""

import json

from medicaidiq.agent import MedicaidIQAgent
from medicaidiq.llm import AssistantTurn, ScriptedLLM, tool_turn
from medicaidiq.memory import WorkingMemory
from medicaidiq.verify import verify_answer


def _answer(summary, findings, answer_type="answer"):
    return {"answer_type": answer_type, "summary": summary, "findings": findings, "confidence": "high",
            "confidence_reason": "r", "caveats": ["synthetic data"], "human_review": []}


def test_happy_path_multi_step(toolbox):
    llm = ScriptedLLM([
        tool_turn("Thought: need the authoritative date.", "get_policy_timeline",
                  {"state": "EV", "topic": "glp1_prior_auth"}),
        tool_turn("Thought: check coverage.", "check_data_coverage", {"state": "EV"}),
        tool_turn("Thought: run pre/post.", "pre_post_analysis",
                  {"state": "EV", "drug_class": "GLP1", "effective_date": "2024-07-01"}),
        tool_turn("Thought: done.", "submit_answer", _answer(
            "GLP-1 prescriptions per 1,000 member-months fell 18.6% after prior authorization began.",
            [{"statement": "Change of -18.6% (95% CI -20.8% to -16.2%).", "evidence_ids": ["E3"]}])),
    ])
    res = MedicaidIQAgent(llm, toolbox).run("Did EV GLP-1 use change after PA?")
    assert res.stop_reason == "submitted" and res.verified
    assert [t.action for t in res.trace] == ["get_policy_timeline", "check_data_coverage",
                                            "pre_post_analysis", "submit_answer"]
    # Observation from step 1 (the resolved date) is visible to the model at step 2.
    assert "2024-07-01" in json.dumps(llm.seen_messages[1])
    # Working memory scratchpad is re-injected.
    assert "[Working memory]" in json.dumps(llm.seen_messages[1])


def test_ungrounded_number_is_caught_and_corrected(toolbox):
    llm = ScriptedLLM([
        tool_turn("t", "pre_post_analysis", {"state": "EV", "drug_class": "GLP1", "effective_date": "2024-07-01"}),
        tool_turn("t", "submit_answer", _answer("Use fell 25.3%.", [{"statement": "fell 25.3%", "evidence_ids": ["E1"]}])),
        tool_turn("t", "submit_answer", _answer("Use fell 18.6%.", [{"statement": "fell 18.6%", "evidence_ids": ["E1"]}])),
    ])
    res = MedicaidIQAgent(llm, toolbox).run("q")
    assert res.verified and res.steps == 3
    assert "verification_failed" in res.trace[1].observation


def test_tool_error_is_observed_and_recovered(toolbox):
    llm = ScriptedLLM([
        tool_turn("t", "run_sql", {"sql": "SELECT no_such_column FROM enrollment"}),
        tool_turn("t", "run_sql", {"sql": "SELECT COUNT(*) AS n FROM enrollment"}),
        tool_turn("t", "submit_answer", _answer("There are 35 enrollment rows.",
                                                [{"statement": "35 rows", "evidence_ids": ["E1"]}])),
    ])
    res = MedicaidIQAgent(llm, toolbox).run("q")
    assert "error" in res.trace[0].observation and res.verified


def test_clarification_stops_non_interactive_run(toolbox):
    llm = ScriptedLLM([tool_turn("Thought: which state?", "ask_user", {"question": "Which state?"})])
    res = MedicaidIQAgent(llm, toolbox).run("Did spending go up after the policy?")
    assert res.stop_reason == "clarification" and res.answer["answer_type"] == "clarification_needed"


def test_clarification_interactive_continues(toolbox):
    llm = ScriptedLLM([
        tool_turn("t", "ask_user", {"question": "Which state?"}),
        tool_turn("t", "check_data_coverage", {"state": "SP"}),
        tool_turn("t", "submit_answer", _answer("SP is missing 2024Q3.",
                                                [{"statement": "2024Q3 missing", "evidence_ids": ["E1"]}])),
    ])
    res = MedicaidIQAgent(llm, toolbox, clarify=lambda q: "Southport").run("q")
    assert res.stop_reason == "submitted"
    assert "Southport" in json.dumps(llm.seen_messages[1])


def test_max_steps_produces_honest_failure(toolbox):
    llm = ScriptedLLM([tool_turn("t", "calculate", {"expression": f"{i}+1"}, f"c{i}") for i in range(5)])
    res = MedicaidIQAgent(llm, toolbox, max_steps=3).run("q")
    assert res.stop_reason == "max_steps" and res.answer["answer_type"] == "cannot_answer"


def test_repeated_identical_failure_stops_loop(toolbox):
    bad = {"sql": "SELECT * FROM secret_table"}
    llm = ScriptedLLM([tool_turn("t", "run_sql", bad, f"c{i}") for i in range(6)])
    res = MedicaidIQAgent(llm, toolbox).run("q")
    assert res.stop_reason == "repeated_failure" and res.steps == 3


def test_prose_answer_is_nudged_to_structured_output(toolbox):
    llm = ScriptedLLM([
        AssistantTurn("The answer is probably yes.", []),
        tool_turn("t", "submit_answer", _answer("No data.", [], "cannot_answer")),
    ])
    res = MedicaidIQAgent(llm, toolbox).run("q")
    assert res.stop_reason == "submitted" and res.trace[0].action == "(no tool call)"


import pytest

from medicaidiq.agent import write_intent


@pytest.mark.parametrize("q", [
    "Delete the provisional 2025 rows from drug_utilization so they stop skewing my results.",
    "Please drop table enrollment",
    "Insert a new record for Westmoor into the database",
    "Update the enrollment table to fix 2024Q3",
    "wipe the data for Southport",
])
def test_write_intent_detected(q):
    assert write_intent(q)


@pytest.mark.parametrize("q", [
    "Did enrollment drop after the policy?",
    "Did GLP-1 prescriptions drop in 2024 after the data were finalized?",
    "Exclude the provisional quarters from the analysis",
    "Which records are suppressed in Southport?",
    "How did the update to Eastvale's PA policy change prescribing?",
])
def test_analysis_questions_not_blocked(q):
    assert not write_intent(q)


def test_write_guardrail_refuses_without_calling_llm(toolbox):
    llm = ScriptedLLM([])
    res = MedicaidIQAgent(llm, toolbox).run("Delete all rows from the enrollment table")
    assert res.stop_reason == "guardrail" and res.answer["answer_type"] == "cannot_answer"
    assert llm.seen_messages == []          # the model was never called
    assert "read-only" in res.answer["summary"]


def test_no_retrieval_ablation_hides_search_tool(toolbox):
    llm = ScriptedLLM([tool_turn("t", "search_policy", {"query": "GLP-1"}),
                       tool_turn("t", "submit_answer", _answer("Cannot verify.", [], "cannot_answer"))])
    agent = MedicaidIQAgent(llm, toolbox, retrieval=False)
    assert "search_policy" not in agent.offered
    res = agent.run("What are Eastvale's PA criteria?")
    assert "not available" in res.trace[0].observation["error"]


def test_retrieved_passage_numbers_are_grounded(toolbox):
    llm = ScriptedLLM([
        tool_turn("t", "search_policy", {"query": "Eastvale GLP-1 prior authorization criteria", "state": "EV"}),
        tool_turn("t", "submit_answer", _answer(
            "Eastvale requires A1c >= 7.0 after metformin, or BMI >= 35 with a comorbidity [EV-MAN-2024-PH#p1].",
            [{"statement": "BMI >= 35 pathway [EV-MAN-2024-PH#p1]", "evidence_ids": ["E1"]}])),
    ])
    res = MedicaidIQAgent(llm, toolbox).run("q")
    assert res.verified and res.trace[0].observation["retrieval_quality"]["status"] == "ok"


def test_truncated_answer_json_is_rejected_not_accepted(toolbox):
    """Regression (live DeepSeek run): a cut-off submit_answer must never be accepted unverified."""
    from medicaidiq.llm import ToolCall
    cut_off = AssistantTurn("t", [ToolCall("c1", "submit_answer", '{"answer_type": "answer", "summary": "GLP-1 rose 38')])
    llm = ScriptedLLM([cut_off, tool_turn("t", "submit_answer", _answer("No data.", [], "cannot_answer"), "c2")])
    res = MedicaidIQAgent(llm, toolbox).run("q")
    assert "not valid JSON" in res.trace[0].observation["error"]
    assert res.answer["answer_type"] == "cannot_answer" and res.steps == 2


def test_citations_are_not_treated_as_numeric_claims():
    from medicaidiq.verify import _claim_numbers
    text = "Per 42 CFR 430.20(b) and § 447.512, see [42-CFR-456.705(b)-2]; prescriptions rose 38.8%."
    assert [v for v, _ in _claim_numbers(text)] == [38.8]


def test_verifier_rules():
    m = WorkingMemory("q")
    m.add_evidence("pre_post_analysis", "s", {"pct_change": -18.63, "pre_mean": 12.04})
    ok = _answer("Rate fell 18.6% (from 12.04) in 2024Q3 across 4 quarters.",
                 [{"statement": "fell about 19%", "evidence_ids": ["E1"]}])
    assert verify_answer(ok, m) == []
    bad = _answer("Rate fell 22.0%.", [{"statement": "x", "evidence_ids": ["E9"]}])
    problems = verify_answer(bad, m)
    assert any("22.0" in p for p in problems) and any("E9" in p for p in problems)
