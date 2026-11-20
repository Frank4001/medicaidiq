"""LLM access through the OpenAI-compatible Chat Completions API.

DeepSeek, Qwen (Alibaba Model Studio), Kimi (Moonshot), and a local llama.cpp
server all speak this protocol, so switching providers is a config change, not a
code change. That also gives the demo a fallback provider (reliability).

Prices are USD per 1M tokens as researched 2026-11-20 (see docs/llm_selection.md).
They are used only for cost logging; verify against provider pages before budgeting.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Iterable

PROVIDERS = {
    "deepseek": {"base_url": "https://api.deepseek.com", "key_env": "DEEPSEEK_API_KEY",
                 "model": "deepseek-flash", "price": {"in": 0.30, "cached_in": 0.006, "out": 1.20}},
    "qwen": {"base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
             "key_env": "DASHSCOPE_API_KEY", "model": "qwen-plus",
             "price": {"in": 0.40, "cached_in": 0.40, "out": 1.20}},
    "kimi": {"base_url": "https://api.moonshot.ai/v1", "key_env": "MOONSHOT_API_KEY",
             "model": "kimi-k2.6", "price": {"in": 0.95, "cached_in": 0.16, "out": 4.00}},
    "local": {"base_url": "http://127.0.0.1:8080/v1", "key_env": "", "model": "local",
              "price": {"in": 0.0, "cached_in": 0.0, "out": 0.0}},
}


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string as produced by the model


@dataclass
class AssistantTurn:
    content: str
    tool_calls: list[ToolCall]
    reasoning: str = ""
    usage: dict = field(default_factory=dict)
    latency_s: float = 0.0

    def as_message(self) -> dict:
        msg: dict = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [{"id": c.id, "type": "function",
                                  "function": {"name": c.name, "arguments": c.arguments}}
                                 for c in self.tool_calls]
        return msg


@dataclass
class UsageMeter:
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    seconds: float = 0.0

    def add(self, usage: dict, seconds: float) -> None:
        self.calls += 1
        self.seconds += seconds
        self.prompt_tokens += usage.get("prompt_tokens", 0)
        self.completion_tokens += usage.get("completion_tokens", 0)
        self.cached_tokens += usage.get("cached_tokens", 0)

    def cost_usd(self, price: dict) -> float:
        uncached = self.prompt_tokens - self.cached_tokens
        return round((uncached * price["in"] + self.cached_tokens * price["cached_in"]
                      + self.completion_tokens * price["out"]) / 1e6, 6)


class ChatLLM:
    """Thin wrapper over an OpenAI-compatible endpoint, with retries and usage metering."""

    def __init__(self, provider: str | None = None, model: str | None = None,
                 base_url: str | None = None, api_key: str | None = None, temperature: float = 0.0,
                 timeout_s: float = 120.0, max_retries: int = 2):
        from openai import OpenAI  # imported lazily so tests run without network config

        self.provider = provider or os.getenv("MEDICAIDIQ_PROVIDER", "deepseek")
        if self.provider not in PROVIDERS:
            raise ValueError(f"Unknown provider {self.provider}; choose from {list(PROVIDERS)}")
        cfg = PROVIDERS[self.provider]
        self.model = model or os.getenv("MEDICAIDIQ_MODEL") or cfg["model"]
        key = api_key or (os.getenv(cfg["key_env"]) if cfg["key_env"] else "not-needed")
        if not key:
            raise RuntimeError(f"Set {cfg['key_env']} in .env to use provider '{self.provider}'.")
        self.price = cfg["price"]
        self.temperature = temperature
        self.meter = UsageMeter()
        self.client = OpenAI(base_url=base_url or os.getenv("MEDICAIDIQ_BASE_URL") or cfg["base_url"],
                             api_key=key, timeout=timeout_s, max_retries=max_retries)

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"

    def chat(self, messages: list[dict], tools: Iterable[dict] | None = None,
             max_tokens: int = 2048) -> AssistantTurn:
        kwargs = dict(model=self.model, messages=messages, temperature=self.temperature,
                      max_tokens=max_tokens)
        if tools:
            kwargs.update(tools=list(tools), tool_choice="auto")
        t0 = time.perf_counter()
        resp = self.client.chat.completions.create(**kwargs)
        dt = time.perf_counter() - t0
        msg = resp.choices[0].message
        usage = {}
        if resp.usage:
            usage = {"prompt_tokens": resp.usage.prompt_tokens or 0,
                     "completion_tokens": resp.usage.completion_tokens or 0}
            details = getattr(resp.usage, "prompt_tokens_details", None)
            cached = getattr(details, "cached_tokens", None) if details else None
            cached = cached or getattr(resp.usage, "prompt_cache_hit_tokens", 0) or 0
            usage["cached_tokens"] = cached
        self.meter.add(usage, dt)
        calls = [ToolCall(c.id, c.function.name, c.function.arguments or "{}") for c in (msg.tool_calls or [])]
        reasoning = getattr(msg, "reasoning_content", None) or ""
        return AssistantTurn(msg.content or "", calls, reasoning, usage, dt)


class ScriptedLLM:
    """Deterministic stand-in for tests and offline demos: replays a fixed list of turns."""

    def __init__(self, turns: list[AssistantTurn]):
        self.turns = list(turns)
        self.meter = UsageMeter()
        self.price = PROVIDERS["local"]["price"]
        self.label = "scripted"
        self.seen_messages: list[list[dict]] = []

    def chat(self, messages, tools=None, max_tokens=2048) -> AssistantTurn:
        self.seen_messages.append(json.loads(json.dumps(messages)))
        if not self.turns:
            return AssistantTurn("Out of scripted turns.", [])
        turn = self.turns.pop(0)
        self.meter.add({"prompt_tokens": 0, "completion_tokens": 0}, 0.0)
        return turn


def tool_turn(thought: str, name: str, args: dict, call_id: str = "") -> AssistantTurn:
    """Helper for building scripted tool-calling turns."""
    return AssistantTurn(thought, [ToolCall(call_id or f"call_{name}", name, json.dumps(args))])
