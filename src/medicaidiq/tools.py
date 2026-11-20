"""Tool registry: typed schemas + dispatch. Each tool addresses a named LLM limitation.

| Tool                  | Limitation it addresses                                   |
|-----------------------|-----------------------------------------------------------|
| search_policy         | Outdated/absent knowledge of state policies (grounding)   |
| get_policy_timeline   | Supersession and conflicting versions (long-term memory)  |
| check_data_coverage   | Missing/provisional data mistaken for real change (F1)    |
| run_sql               | No access to data; unsafe or invalid SQL                  |
| pre_post_analysis     | Arithmetic/statistics errors; significance vs materiality |
| spending_decomposition| Spending change mistaken for change in care (F1)          |
| calculate             | Token-level arithmetic errors ("9.11 > 9.9")              |
| ask_user              | Guessing when the question is ambiguous (elicitation)     |
| submit_answer         | Unstructured, unverifiable final output                   |
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .db import ReadOnlyDB, SQLRejected
from .memory import KnowledgeStore, WorkingMemory
from .retrieval import PolicySearch
from . import stats

TOPICS = ["glp1_coverage", "glp1_prior_auth", "sglt2_pdl"]
STATE_ENUM = ["NL", "EV", "SP"]
DRUG_ENUM = ["GLP1", "SGLT2", "INSULIN"]


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required,
                       "additionalProperties": False}}}


TOOL_SCHEMAS = [
    _fn("search_policy",
        "Keyword search over state Medicaid policy documents (SPAs, bulletins, manuals, federal guidance). "
        "Returns document metadata (doc_type, published, effective_date, supersedes/superseded_by) and text.",
        {"query": {"type": "string", "description": "Search terms, e.g. 'GLP-1 prior authorization'"},
         "state": {"type": "string", "enum": STATE_ENUM, "description": "Optional state filter"},
         "as_of": {"type": "string", "description": "Optional YYYY-MM-DD; only documents published by then"}},
        ["query"]),
    _fn("get_policy_timeline",
        "Resolve the authoritative effective date for a policy from long-term memory, applying supersession "
        "and source-authority rules. Reports conflicts and superseded versions. Use before any pre/post analysis.",
        {"state": {"type": "string", "enum": STATE_ENUM},
         "topic": {"type": "string", "enum": TOPICS}},
        ["state", "topic"]),
    _fn("check_data_coverage",
        "List which quarters are final, provisional (claims lag), or missing for a state, plus suppressed rows.",
        {"state": {"type": "string", "description": "State code"}}, ["state"]),
    _fn("run_sql",
        "Run ONE read-only SELECT query against the approved tables (schema in system prompt). "
        "Max 200 rows. Use for descriptive lookups; use pre_post_analysis for before/after comparisons.",
        {"sql": {"type": "string"}}, ["sql"]),
    _fn("pre_post_analysis",
        "Compare a utilization or spending metric before vs after a policy effective date. Excludes provisional/"
        "missing quarters, returns percent change with bootstrap 95% CI and a significance x materiality verdict.",
        {"state": {"type": "string", "enum": STATE_ENUM},
         "drug_class": {"type": "string", "enum": DRUG_ENUM},
         "effective_date": {"type": "string", "description": "YYYY-MM-DD, from get_policy_timeline"},
         "metric": {"type": "string", "enum": list(stats.METRICS), "description": "default rx_per_1000_mm"},
         "materiality_pct": {"type": "number", "description": "Minimum % change that matters; default 5"}},
        ["state", "drug_class", "effective_date"]),
    _fn("spending_decomposition",
        "Split the change in total drug spending between two final quarters into enrollment, utilization per "
        "member, and price per prescription. Use whenever spending changed, to avoid equating spending with care.",
        {"state": {"type": "string", "enum": STATE_ENUM}, "drug_class": {"type": "string", "enum": DRUG_ENUM},
         "start_quarter": {"type": "string", "description": "e.g. 2023Q2"},
         "end_quarter": {"type": "string", "description": "e.g. 2025Q2"}},
        ["state", "drug_class", "start_quarter", "end_quarter"]),
    _fn("calculate", "Exact arithmetic on numbers (+ - * / ** parentheses). Never do math in your head.",
        {"expression": {"type": "string"}}, ["expression"]),
    _fn("ask_user",
        "Ask the user ONE clarifying question when the state, drug class, or policy is ambiguous. "
        "Do not guess missing essentials.",
        {"question": {"type": "string"}}, ["question"]),
    _fn("submit_answer",
        "Submit the final structured answer. Every number in summary/findings must come from a tool result, "
        "and each finding must cite evidence ids (E1, E2, ...) returned by tools. For cannot_answer or "
        "clarification_needed without tool evidence, use an empty findings list.",
        {"answer_type": {"type": "string", "enum": ["answer", "cannot_answer", "clarification_needed"]},
         "summary": {"type": "string", "description": "2-4 sentences, plain language"},
         "findings": {"type": "array", "items": {
             "type": "object", "additionalProperties": False,
             "properties": {"statement": {"type": "string"},
                            "evidence_ids": {"type": "array", "items": {"type": "string"}}},
             "required": ["statement", "evidence_ids"]}},
         "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
         "confidence_reason": {"type": "string"},
         "caveats": {"type": "array", "items": {"type": "string"}},
         "human_review": {"type": "array", "items": {"type": "string"},
                          "description": "Items a human analyst should verify"}},
        ["answer_type", "summary", "findings", "confidence", "confidence_reason", "caveats"]),
]
TOOL_NAMES = {t["function"]["name"] for t in TOOL_SCHEMAS}
TERMINAL_TOOLS = {"submit_answer", "ask_user"}


@dataclass
class Toolbox:
    db: ReadOnlyDB
    search: PolicySearch = field(default_factory=PolicySearch)
    knowledge: KnowledgeStore = field(default_factory=KnowledgeStore)

    def execute(self, name: str, raw_args: str, memory: WorkingMemory) -> dict:
        """Run a tool; errors become observations so the agent can recover (never raise)."""
        try:
            args = json.loads(raw_args or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            memory.record_failure(f"{name}: invalid JSON arguments")
            return {"error": f"Invalid JSON arguments: {exc}. Retry with valid JSON."}
        if name not in TOOL_NAMES or name in TERMINAL_TOOLS:
            return {"error": f"Unknown tool '{name}'."}
        try:
            result = getattr(self, f"_t_{name}")(memory, **args)
        except TypeError as exc:
            result = {"error": f"Bad arguments for {name}: {exc}"}
        except SQLRejected as exc:
            result = {"error": str(exc)}
        if "error" in result:
            memory.record_failure(f"{name}({json.dumps(args)[:120]}): {result['error'][:120]}")
            return result
        eid = memory.add_evidence(name, _summarize(name, args, result), result)
        return {"evidence_id": eid, **result}

    # --- tool implementations --------------------------------------------------------------
    def _t_search_policy(self, memory, query: str, state: str | None = None, as_of: str | None = None):
        hits = self.search.search(query, state=state, as_of=as_of)
        if not hits:
            return {"error": "No matching documents. Try different terms or remove filters."}
        memory.update_slots(state=state)
        return {"documents": hits}

    def _t_get_policy_timeline(self, memory, state: str, topic: str):
        res = self.knowledge.resolve(f"{state.upper()}:{topic}:effective_date")
        if res.status == "unknown":
            return {"error": f"No policy on record for {state}/{topic}. Search documents instead; "
                             "if none exist, the policy may not exist for this state."}
        memory.update_slots(state=state.upper(), topic=topic, effective_date=res.value)
        return res.to_dict()

    def _t_check_data_coverage(self, memory, state: str):
        return stats.data_coverage(self.db, state)

    def _t_run_sql(self, memory, sql: str):
        return self.db.query(sql).to_dict()

    def _t_pre_post_analysis(self, memory, state, drug_class, effective_date,
                             metric="rx_per_1000_mm", materiality_pct=5.0):
        memory.update_slots(state=state, drug_class=drug_class, effective_date=effective_date, metric=metric)
        return stats.pre_post_analysis(self.db, state, drug_class, effective_date, metric,
                                       materiality_pct=float(materiality_pct))

    def _t_spending_decomposition(self, memory, state, drug_class, start_quarter, end_quarter):
        return stats.spending_decomposition(self.db, state, drug_class, start_quarter, end_quarter)

    def _t_calculate(self, memory, expression: str):
        return stats.calculate(expression)


def _summarize(name: str, args: dict, result: dict) -> str:
    if name == "pre_post_analysis" and result.get("status") == "ok":
        return (f"{result['state']} {result['drug_class']} {result['metric']} {result['pct_change']}% "
                f"CI{result['ci95_pct_change']} {result['verdict']}")
    if name == "get_policy_timeline":
        return f"{result['key']} -> {result['value']} ({result['status']}, {result['source_doc']})"
    if name == "search_policy":
        return "docs " + ",".join(d["doc_id"] for d in result["documents"])
    if name == "run_sql":
        return f"{result['row_count']} rows"
    if name == "spending_decomposition":
        return f"spend {result['total_spending_pct_change']}% = {result['component_pct_change']}"
    return json.dumps(result)[:100]
