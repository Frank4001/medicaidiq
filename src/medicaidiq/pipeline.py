"""One entry point for every caller (CLI, eval harness, demo UI).

answer_question(question, mode=...) runs the chosen workflow, then applies the Week 6 safety layers
in a fixed order:
  1. agent / orchestrator run (input guardrails, tool guardrails, verifier with calibration check)
  2. trajectory monitor        -> suspicion score + signals
  3. oversight policy          -> released, or withheld + review ticket (SLA)
  4. structured, PII-redacted log line in runs/events.jsonl
"""

from __future__ import annotations

from .agent import AgentResult, MedicaidIQAgent
from .monitor import log_run, score_trajectory
from .oversight import ReviewQueue, apply_oversight
from .tools import Toolbox

MODES = {"single": "Single agent (default)", "multi": "Four-agent workflow",
         "tot": "Single agent + Tree-of-Thought planner", "no_retrieval": "Single agent without retrieval (ablation)"}


def build_runner(mode: str, llm, toolbox: Toolbox, clarify=None):
    if mode == "multi":
        from .multiagent import MultiAgentOrchestrator
        return MultiAgentOrchestrator(llm, toolbox)
    return MedicaidIQAgent(llm, toolbox, clarify=clarify, planning=(mode == "tot"),
                           retrieval=(mode != "no_retrieval"))


def answer_question(question: str, *, mode: str = "single", llm=None, toolbox: Toolbox | None = None,
                    provider: str | None = None, model: str | None = None, queue: ReviewQueue | None = None,
                    oversight: bool = True, log: bool = True, clarify=None, run_label: str = "") -> AgentResult:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {list(MODES)}")
    if llm is None:
        from .llm import ChatLLM
        llm = ChatLLM(provider=provider, model=model)
    if toolbox is None:
        from .db import ReadOnlyDB
        toolbox = Toolbox(ReadOnlyDB())
    runner = build_runner(mode, llm, toolbox, clarify)
    result = runner.run(question)
    result.usage["mode"] = mode
    result.monitor = score_trajectory(result)
    if oversight:
        memory = getattr(runner, "last_memory", None)
        apply_oversight(result, queue if queue is not None else ReviewQueue(), result.monitor,
                        memory.evidence if memory else [], run_label or mode)
    if log:
        log_run(result, {"mode": mode, "model": result.usage.get("model")})
    return result
