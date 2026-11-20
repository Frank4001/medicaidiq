"""The MedicaidIQ ReAct agent (Week 2).

Loop: Thought -> Action (native tool call) -> Observation, repeated until the agent
submits an answer, asks for clarification, or hits a limit. Compared with the lab's
regex-parsed ReAct, native tool calling gives schema-validated arguments, which is
the reliability gain the tool-calling lab demonstrated for API models.

Stop conditions: submit_answer passes verification | ask_user without a reply
| max_steps reached | repeated identical failing call | write-intent guardrail (before any LLM call).
Self-correction: tool errors and verifier failures come back as observations.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Callable

from .llm import AssistantTurn
from .memory import WorkingMemory
from .prompts import SYSTEM_PROMPT
from .tools import Toolbox, tool_schemas
from .verify import verify_answer

MAX_OBS_CHARS = 6000

# Deterministic guardrail: requests to change stored data are refused before any LLM call.
# The model refusing is not enough (local Qwen3-8B failed this case twice; see llm_selection.md).
# "drop" alone is NOT a write verb ("Did enrollment drop?" is an analysis question).
_WRITE_VERB = r"(delete|truncate|insert|overwrite|alter|purge|wipe|erase)"
_DATA_OBJ = r"(rows?|records?|tables?|database|data(set)?|entries|values|drug_utilization|enrollment)"
WRITE_INTENT = re.compile(rf"\b{_WRITE_VERB}\b[^.?!]{{0,60}}\b{_DATA_OBJ}\b"
                          rf"|\bdrop\s+(the\s+)?(table|database)\b"
                          rf"|\b(update|modify|edit)\b[^.?!]{{0,40}}\b(table|database|rows?|records?)\b",
                          re.IGNORECASE)


def write_intent(question: str) -> bool:
    return bool(WRITE_INTENT.search(question))


@dataclass
class TraceStep:
    step: int
    thought: str
    action: str
    arguments: dict
    observation: dict
    latency_s: float


@dataclass
class AgentResult:
    question: str
    answer: dict
    trace: list[TraceStep]
    evidence: list[dict]
    stop_reason: str
    verified: bool
    steps: int
    usage: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class MedicaidIQAgent:
    def __init__(self, llm, toolbox: Toolbox, max_steps: int = 10, max_verify_retries: int = 2,
                 clarify: Callable[[str], str | None] | None = None, retrieval: bool = True):
        self.llm = llm
        self.tools = toolbox
        self.max_steps = max_steps
        self.max_verify_retries = max_verify_retries
        self.clarify = clarify  # None -> non-interactive: stop and return the clarifying question
        self.schemas = tool_schemas(retrieval)
        self.offered = {t["function"]["name"] for t in self.schemas}

    def run(self, question: str) -> AgentResult:
        memory = WorkingMemory(question)
        if write_intent(question):
            answer = {"answer_type": "cannot_answer",
                      "summary": "I can't modify, delete, or insert data: MedicaidIQ has read-only access by design. "
                                 "To leave out provisional or problem periods, ask me to exclude them from an analysis "
                                 "instead (pre/post analyses already exclude provisional quarters).",
                      "findings": [], "confidence": "high",
                      "confidence_reason": "Deterministic read-only guardrail (no model call).",
                      "caveats": [], "human_review": []}
            trace = [TraceStep(0, "Guardrail: write-intent detected before any model call.", "guardrail_refusal",
                               {}, {"blocked": True}, 0.0)]
            return self._result(question, answer, trace, memory, "guardrail", True, 0)
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": question}]
        trace: list[TraceStep] = []
        verify_retries = 0
        last_failed_call = None
        repeat_failures = 0

        for step in range(1, self.max_steps + 1):
            turn: AssistantTurn = self.llm.chat(messages, tools=self.schemas, max_tokens=4096)
            thought = (turn.content or turn.reasoning or "").strip()
            messages.append(turn.as_message())

            if not turn.tool_calls:
                # Model answered in prose instead of submit_answer: nudge once, it's a format error.
                trace.append(TraceStep(step, thought, "(no tool call)", {}, {}, turn.latency_s))
                messages.append({"role": "user", "content":
                                 "Observation: You must finish by calling submit_answer (or ask_user). "
                                 "Call it now with your structured answer."})
                continue

            for call in turn.tool_calls:
                try:
                    args = json.loads(call.arguments or "{}")
                    parsed = isinstance(args, dict)
                except json.JSONDecodeError:
                    args, parsed = {"_raw": (call.arguments or "")[:500]}, False

                if call.name == "submit_answer" and not parsed:
                    # Never accept an answer we cannot parse and verify (seen live: output cut off mid-JSON).
                    obs = {"error": "submit_answer arguments were not valid JSON (possibly cut off because the "
                                    "answer was too long). Resubmit valid JSON with a shorter summary and fewer, "
                                    "more concise findings."}
                    memory.record_failure("submit_answer: invalid JSON")
                    trace.append(TraceStep(step, thought, call.name, args, obs, turn.latency_s))
                    messages.append(_tool_msg(call.id, obs))
                    continue

                if call.name == "submit_answer":
                    problems = verify_answer(args, memory)
                    if problems and verify_retries < self.max_verify_retries:
                        verify_retries += 1
                        obs = {"verification_failed": problems,
                               "instruction": "Fix these issues and call submit_answer again."}
                        trace.append(TraceStep(step, thought, call.name, args, obs, turn.latency_s))
                        messages.append(_tool_msg(call.id, obs))
                        continue
                    if problems:
                        args.setdefault("caveats", []).append(
                            "Automatic verification did not fully pass: " + " ".join(problems))
                    trace.append(TraceStep(step, thought, call.name, args, {"accepted": True}, turn.latency_s))
                    return self._result(question, args, trace, memory, "submitted", not problems, step)

                if call.name == "ask_user":
                    q = args.get("question", "Could you clarify your question?")
                    reply = self.clarify(q) if self.clarify else None
                    trace.append(TraceStep(step, thought, call.name, args,
                                           {"user_reply": reply}, turn.latency_s))
                    if reply is None:
                        answer = {"answer_type": "clarification_needed", "summary": q, "findings": [],
                                  "confidence": "low", "confidence_reason": "Question is ambiguous.",
                                  "caveats": [], "human_review": []}
                        return self._result(question, answer, trace, memory, "clarification", True, step)
                    messages.append(_tool_msg(call.id, {"user_reply": reply}))
                    continue

                if call.name not in self.offered:
                    obs = {"error": f"Tool '{call.name}' is not available in this configuration."}
                    memory.record_failure(f"{call.name}: not available")
                else:
                    obs = self.tools.execute(call.name, call.arguments, memory)
                trace.append(TraceStep(step, thought, call.name, args, obs, turn.latency_s))
                messages.append(_tool_msg(call.id, obs))

                signature = (call.name, call.arguments)
                if "error" in obs:
                    repeat_failures = repeat_failures + 1 if signature == last_failed_call else 0
                    last_failed_call = signature
                    if repeat_failures >= 2:
                        return self._result(question, _limit_answer(memory, "the same tool call kept failing"),
                                            trace, memory, "repeated_failure", False, step)

            # Re-inject the compact working-memory scratchpad so key slots survive long contexts.
            messages.append({"role": "user", "content": "[Working memory]\n" + memory.scratchpad()})

        return self._result(question, _limit_answer(memory, f"the {self.max_steps}-step limit was reached"),
                            trace, memory, "max_steps", False, self.max_steps)

    def _result(self, question, answer, trace, memory, reason, verified, steps) -> AgentResult:
        m = self.llm.meter
        usage = {"model": getattr(self.llm, "label", "?"), "llm_calls": m.calls,
                 "prompt_tokens": m.prompt_tokens, "cached_tokens": m.cached_tokens,
                 "completion_tokens": m.completion_tokens, "llm_seconds": round(m.seconds, 2),
                 "cost_usd": m.cost_usd(self.llm.price)}
        evidence = [{"id": e.id, "tool": e.kind, "summary": e.summary} for e in memory.evidence]
        return AgentResult(question, answer, trace, evidence, reason, verified, steps, usage)


def _tool_msg(call_id: str, payload: dict) -> dict:
    text = json.dumps(payload, default=str)
    if len(text) > MAX_OBS_CHARS:
        text = text[:MAX_OBS_CHARS] + '..."(truncated)"'
    return {"role": "tool", "tool_call_id": call_id, "content": text}


def _limit_answer(memory: WorkingMemory, why: str) -> dict:
    return {"answer_type": "cannot_answer",
            "summary": f"I could not complete this analysis because {why}.",
            "findings": [], "confidence": "low", "confidence_reason": why,
            "caveats": ["Failed attempts: " + "; ".join(memory.failed_attempts[-3:])] if memory.failed_attempts else [],
            "human_review": ["Review the trace to see where the analysis stalled."]}
