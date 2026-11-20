"""MedicaidIQ tools as an MCP server (Week 1 Lab 2 + Week 2 Lab 2). Built with MCP SDK 2.x MCPServer.

Exposes the same deterministic tools the agent uses, so any MCP host (Claude Desktop,
VS Code, Cursor) can call them. Primitives follow the Week 1 lab's control model:
  Tools     (model-controlled):       search_policy, get_policy_timeline, check_data_coverage,
                                      run_sql, pre_post_analysis, spending_decomposition, calculate
  Resources (application-controlled): schema://medicaidiq, policy://{doc_id}
  Prompts   (user-controlled):        pre_post_policy_analysis

Run:  python -m medicaidiq.mcp_server      (stdio transport, local and read-only)
"""

from __future__ import annotations

import json

from mcp.server.mcpserver import MCPServer  # MCP SDK 2.x name for the labs' FastMCP

from .db import SCHEMA_DOC, ReadOnlyDB
from .memory import WorkingMemory
from .paths import POLICY_DIR
from .tools import Toolbox

mcp = MCPServer("medicaidiq")
_toolbox: Toolbox | None = None


def _tb() -> Toolbox:
    global _toolbox
    if _toolbox is None:
        _toolbox = Toolbox(ReadOnlyDB())
    return _toolbox


def _call(name: str, **kwargs) -> str:
    args = {k: v for k, v in kwargs.items() if v is not None}
    return json.dumps(_tb().execute(name, json.dumps(args), WorkingMemory(f"mcp:{name}")), default=str)


@mcp.tool()
def search_policy(query: str, state: str | None = None, as_of: str | None = None,
                  include_federal: bool = True) -> str:
    """Hybrid semantic + keyword search over state policy documents and federal regulations (42 CFR)."""
    return _call("search_policy", query=query, state=state, as_of=as_of, include_federal=include_federal)


@mcp.tool()
def get_policy_timeline(state: str, topic: str) -> str:
    """Authoritative policy effective date after supersession/authority rules (topics: glp1_coverage, glp1_prior_auth, sglt2_pdl)."""
    return _call("get_policy_timeline", state=state, topic=topic)


@mcp.tool()
def check_data_coverage(state: str) -> str:
    """Final / provisional / missing quarters and suppressed rows for a state."""
    return _call("check_data_coverage", state=state)


@mcp.tool()
def run_sql(sql: str) -> str:
    """One read-only SELECT against approved tables (see schema://medicaidiq). Max 200 rows."""
    return _call("run_sql", sql=sql)


@mcp.tool()
def pre_post_analysis(state: str, drug_class: str, effective_date: str,
                      metric: str = "rx_per_1000_mm", materiality_pct: float = 5.0) -> str:
    """Before/after comparison with bootstrap CI and significance x materiality verdict."""
    return _call("pre_post_analysis", state=state, drug_class=drug_class, effective_date=effective_date,
                 metric=metric, materiality_pct=materiality_pct)


@mcp.tool()
def spending_decomposition(state: str, drug_class: str, start_quarter: str, end_quarter: str) -> str:
    """Split a spending change into enrollment, utilization, and price components."""
    return _call("spending_decomposition", state=state, drug_class=drug_class,
                 start_quarter=start_quarter, end_quarter=end_quarter)


@mcp.tool()
def calculate(expression: str) -> str:
    """Exact arithmetic (+ - * / ** parentheses)."""
    return _call("calculate", expression=expression)


@mcp.resource("schema://medicaidiq")
def schema() -> str:
    """Data dictionary for the approved tables."""
    return SCHEMA_DOC


@mcp.resource("policy://{doc_id}")
def policy_document(doc_id: str) -> str:
    """Full text of one policy document by id (e.g. EV-BUL-2024-011 or 42-CFR-430.20)."""
    from .ingest_public import PUBLIC_DIR
    if not all(ch.isalnum() or ch in "-." for ch in doc_id):
        return f"Unknown document id: {doc_id}"
    md, js = POLICY_DIR / f"{doc_id}.md", PUBLIC_DIR / f"{doc_id}.json"
    if md.exists():
        return md.read_text(encoding="utf-8")
    if js.exists():
        d = json.loads(js.read_text(encoding="utf-8"))
        return f"# {d['title']}\nSource: {d['url']} (version {d['version_date']})\n\n" + "\n\n".join(d["paragraphs"])
    return f"Unknown document id: {doc_id}"


@mcp.prompt()
def pre_post_policy_analysis(state: str, drug_class: str, topic: str) -> str:
    """Saved workflow: did utilization change after a policy took effect?"""
    return (f"Using the MedicaidIQ tools: (1) get_policy_timeline for {state}/{topic}; "
            f"(2) check_data_coverage for {state}; (3) pre_post_analysis for {drug_class} using the "
            "resolved effective date; (4) report % change, 95% CI, verdict, excluded quarters, and any "
            "superseded or conflicting sources. Do not claim causation.")


if __name__ == "__main__":
    mcp.run()
