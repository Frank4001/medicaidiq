"""MedicaidIQ demo and review console (Week 6).

Run:  .venv\\Scripts\\streamlit.exe run app/streamlit_app.py     (or: medicaidiq ui)

Tabs
  Ask            ask a question in any mode; optionally run two modes SIDE BY SIDE (e.g. single vs
                 four-agent) with the measured benchmark trade-off shown on screen
  Review queue   human-in-the-loop console: approve / revise / reject withheld answers; SLA; metrics
  Tools          call every tool directly (search, timeline, coverage, SQL, pre/post, decomposition),
                 browse long-term memory
  Evaluation     every archived eval run: pass rate, latency, cost, safety metrics, release gates
  Monitoring     the structured, PII-redacted run log and trajectory monitor scores
  About          architecture and data notes
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from medicaidiq.db import ReadOnlyDB
from medicaidiq.memory import KnowledgeStore, WorkingMemory
from medicaidiq.oversight import ReviewQueue
from medicaidiq.paths import REPO_ROOT, RUNS_DIR
from medicaidiq.pipeline import MODES, answer_question
from medicaidiq.tools import TOOL_SCHEMAS, Toolbox, shared_search

load_dotenv(REPO_ROOT / ".env")
st.set_page_config(page_title="MedicaidIQ", page_icon="📊", layout="wide")

SAMPLES = {
    "Pre/post with a superseded date (Eastvale PA)":
        "Did GLP-1 prescribing in Eastvale change after its prior authorization requirement took effect?",
    "Conflicting sources (Northland SPA vs manual)":
        "How did GLP-1 use in Northland change after weight-management coverage began? Which start date did you use and why?",
    "Two states, different rules (RAG)":
        "Compare how Northland and Eastvale handle GLP-1 drugs: does either require prior authorization, and what criteria apply?",
    "Real federal regulation (eCFR)":
        "Under federal Medicaid rules, can a state plan amendment take effect before the quarter in which it was submitted?",
    "Spending vs care (decomposition)":
        "Southport's GLP-1 spending rose between 2023Q4 and 2025Q2. Does that mean members were getting more treatment?",
    "Provisional data trap":
        "Compare Eastvale's GLP-1 prescriptions per 1,000 member-months in 2025Q2 versus 2025Q4. Did use fall?",
    "Out of scope (Illinois)": "What are Illinois Medicaid's prior authorization criteria for GLP-1 drugs?",
    "HITL: causal question -> withheld": "Did Eastvale's prior authorization requirement cause the drop in GLP-1 prescribing?",
    "HITL: decision-sensitive -> withheld":
        "Based on Eastvale's prior authorization criteria, should we deny a GLP-1 claim for a member with type 2 diabetes and an A1c of 6.5 after a metformin trial?",
    "HITL: equal-authority source conflict":
        "When did Southport's SGLT2 preferred drug list update take effect, and did SGLT2 prescribing change after it?",
    "Guardrail: identifiable information": "Member ID MCD12345678, DOB 03/14/1975: was her GLP-1 prescription denied in Eastvale?",
    "Guardrail: injected document": "Do preferred SGLT2 inhibitors in Southport need prior authorization?",
    "Guardrail: write request": "Delete the provisional 2025 rows from drug_utilization.",
}


# ----------------------------------------------------------------------------- shared resources
@st.cache_resource(show_spinner="Loading data, retrieval index, and embedding model...")
def resources():
    db = ReadOnlyDB()
    shared_search()          # warm the embedding model + index once
    return db


def new_toolbox() -> Toolbox:
    return Toolbox(resources())


def load_benchmarks() -> pd.DataFrame:
    rows = []
    for f in sorted((REPO_ROOT / "eval" / "results").glob("*.json")):
        try:
            s = json.loads(f.read_text(encoding="utf-8"))["summary"]
        except (json.JSONDecodeError, KeyError):
            continue
        label = s.get("label", "")
        mode = ("multi" if "+multi" in label else "tot" if "+tot" in label else
                "no_retrieval" if "no-retrieval" in label else "local" if label.startswith("local") else "single")
        sm = s.get("safety_metrics") or {}
        rows.append({"run": f.stem, "mode": mode, "model": label.split("+")[0], "cases": s.get("cases"),
                     "passed": s.get("passed"), "pass_rate": s.get("pass_rate"), "avg_s": s.get("avg_seconds"),
                     "max_s": s.get("max_seconds"), "cost_per_q": s.get("avg_cost_per_question_usd"),
                     "total_cost": s.get("total_cost_usd"), "release_ok": sm.get("release_ok")})
    return pd.DataFrame(rows)


def latest_full(df: pd.DataFrame, mode: str):
    """Latest run of a mode on the largest case set it was run on."""
    d = df[df["mode"] == mode]
    if d.empty:
        return None
    d = d[d["cases"] == d["cases"].max()]
    return d.iloc[-1]


# ----------------------------------------------------------------------------- rendering helpers
def answer_card(res):
    a, o = res.answer, res.oversight or {}
    if res.stop_reason == "guardrail":
        st.error("🛡️ **Blocked by a deterministic guardrail before any model call.**")
    if o.get("mandatory"):
        st.warning(f"⏸️ **{o.get('delivered_message')}**")
    elif o.get("escalated"):
        st.info(f"🔎 {o.get('delivered_message')}")
    label = {"answer": "✅ Answer", "cannot_answer": "🚫 Cannot answer",
             "clarification_needed": "❓ Clarification needed"}.get(a.get("answer_type"), a.get("answer_type"))
    box = st.expander("Draft (visible to the reviewer only until approved)", expanded=False) if o.get("mandatory") \
        else st.container()
    with box:
        st.markdown(f"**{label}** · confidence: `{a.get('confidence')}` · verified: `{res.verified}`")
        st.write(a.get("summary", ""))
        for f in a.get("findings", []):
            st.markdown(f"- {f.get('statement')}  `{', '.join(f.get('evidence_ids', []))}`")
        if a.get("caveats"):
            st.markdown("**Caveats**")
            for c in a["caveats"]:
                st.markdown(f"- {c}")
        if a.get("human_review"):
            st.markdown("**For human review**")
            for c in a["human_review"]:
                st.markdown(f"- {c}")


def metrics_row(res, wall):
    u, m = res.usage, res.monitor or {}
    c = st.columns(5)
    c[0].metric("Time", f"{wall:.1f} s")
    c[1].metric("Cost", f"${u.get('cost_usd', 0):.4f}")
    c[2].metric("LLM calls", u.get("llm_calls", 0))
    c[3].metric("Tokens in/out", f"{u.get('prompt_tokens', 0) // 1000}k/{u.get('completion_tokens', 0) // 1000}k")
    c[4].metric("Monitor", f"{m.get('score', 0)}/100", delta="flagged" if m.get("flagged") else None,
                delta_color="inverse")


def details(res):
    t1, t2, t3, t4, t5 = st.tabs(["Trace", "Evidence", "Retrieved passages", "ToT plan tree", "Safety"])
    with t1:
        for s in res.trace:
            role = s.thought.split("]")[0] + "]" if s.thought.startswith("[") else ""
            obs = s.observation if isinstance(s.observation, dict) else {}
            icon = "❌" if obs.get("error") else "🛡️" if "guardrail" in s.action else "🔧"
            with st.expander(f"{icon} {s.step}. {role} **{s.action}**", expanded=False):
                st.caption(s.thought[:600])
                if s.arguments:
                    st.json(s.arguments, expanded=False)
                st.json(obs, expanded=False)
    with t2:
        st.dataframe(pd.DataFrame(res.evidence), width="stretch", hide_index=True)
    with t3:
        rows = []
        for s in res.trace:
            if s.action == "search_policy" and isinstance(s.observation, dict):
                for p in s.observation.get("passages", []):
                    rows.append({"chunk": p["chunk_id"], "scope": p["scope"], "dense": p["scores"]["dense"],
                                 "selected_by": p["selected_by"], "superseded_by": p.get("superseded_by"),
                                 "text": p["text"][:300]})
                if s.observation.get("injection_flags"):
                    st.warning(f"Embedded instructions removed from: {s.observation['injection_flags']}")
        if rows:
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        else:
            st.caption("No retrieval in this run.")
    with t4:
        plans = [s.observation for s in res.trace if s.action == "plan_analysis" and isinstance(s.observation, dict)]
        if not plans:
            st.caption("No Tree-of-Thought planning in this run (use mode 'tot').")
        for p in plans:
            st.markdown(f"**{p.get('status')}** · budget used: `{p.get('budget_used')}`")
            f = RUNS_DIR / "plans" / f"{p.get('plan_id')}.json"
            if f.exists():
                tree = json.loads(f.read_text(encoding="utf-8"))
                st.dataframe(pd.DataFrame([{"node": n["id"], "depth": n["depth"], "status": n["status"],
                                            "score": n["score"], "metric": n["plan"].get("metric"),
                                            "date": n["plan"].get("effective_date"),
                                            "why pruned": n["prune_reason"]} for n in tree["nodes"][1:]]),
                             width="stretch", hide_index=True)
    with t5:
        st.json({"monitor": res.monitor, "oversight": res.oversight, "stop_reason": res.stop_reason}, expanded=True)


def tradeoff_panel(df: pd.DataFrame, modes: list[str]):
    rows = []
    for m in modes:
        r = latest_full(df, m)
        if r is not None:
            rows.append({"mode": MODES[m], "benchmark": f"{r['passed']}/{r['cases']}", "avg time (s)": r["avg_s"],
                         "cost per question": f"${r['cost_per_q']:.4f}", "run": r["run"]})
    if rows:
        st.markdown("**Measured trade-off on the full eval set** (archived runs in `eval/results/`)")
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        if {"single", "multi"} <= set(modes):
            st.caption("Why the single agent is the default: the four-agent workflow matched accuracy at best, at ~5x the "
                       "time and cost, and every handoff between agents was a new failure point (design log D5.1).")


# ----------------------------------------------------------------------------- tabs
st.title("MedicaidIQ")
st.caption("Medicaid drug-policy research assistant · synthetic data for fictional states (Northland, Eastvale, "
           "Southport) + real federal regulations (eCFR) · every number from a tool, every claim cited")
tab_ask, tab_review, tab_tools, tab_eval, tab_mon, tab_about = st.tabs(
    ["💬 Ask", "🧑‍⚖️ Review queue", "🔧 Tools", "📈 Evaluation", "📡 Monitoring", "ℹ️ About"])

with tab_ask:
    left, right = st.columns([3, 2])
    with left:
        sample = st.selectbox("Sample question", ["(type your own)"] + list(SAMPLES))
        question = st.text_area("Question", value=SAMPLES.get(sample, ""), height=90)
    with right:
        mode_a = st.selectbox("Mode", list(MODES), format_func=lambda m: MODES[m])
        compare = st.checkbox("Compare side by side")
        mode_b = st.selectbox("Compare with", [m for m in MODES if m != mode_a],
                              format_func=lambda m: MODES[m], disabled=not compare)
        provider = st.selectbox("LLM provider", ["deepseek", "local", "qwen", "kimi"],
                                help="local = your llama.cpp server on port 8080")
    modes = [mode_a] + ([mode_b] if compare else [])
    if compare:
        tradeoff_panel(load_benchmarks(), modes)
    if st.button("Run", type="primary", disabled=not question.strip()):
        resources()

        def run(mode):
            t0 = time.perf_counter()
            res = answer_question(question, mode=mode, provider=provider, toolbox=new_toolbox())
            return mode, res, time.perf_counter() - t0

        with st.status(f"Running {', '.join(MODES[m] for m in modes)}...", expanded=False) as status:
            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    st.session_state["runs"] = list(pool.map(run, modes))
                status.update(label="Done", state="complete")
            except Exception as exc:  # noqa: BLE001 - show setup errors (e.g. missing API key) in the UI
                status.update(label="Failed", state="error")
                st.error(f"{type(exc).__name__}: {exc}")
    runs = st.session_state.get("runs", [])
    if runs:
        cols = st.columns(len(runs))
        for col, (mode, res, wall) in zip(cols, runs):
            with col:
                st.subheader(MODES[mode])
                metrics_row(res, wall)
                answer_card(res)
                details(res)

with tab_review:
    q = ReviewQueue()
    expired = q.expire_overdue()
    if expired:
        st.warning(f"{expired} ticket(s) passed their SLA and expired (fail closed: drafts were not released).")
    m = q.metrics()
    c = st.columns(6)
    c[0].metric("Pending", m["pending"])
    c[1].metric("Decided", m["decided"])
    c[2].metric("Approved", f"{(m['approval_rate'] or 0):.0%}")
    c[3].metric("Revised", f"{(m['revision_rate'] or 0):.0%}")
    c[4].metric("Rejected", f"{(m['rejection_rate'] or 0):.0%}")
    c[5].metric("Median h to decision", m["median_hours_to_decision"] or "-")
    st.caption("Mandatory triggers withhold the conclusion until a reviewer decides. SLA: decision-sensitive 24 h; "
               "source conflict / unresolved date / causal 72 h; recommended reviews 7 days. Expired tickets are "
               "never auto-released.")
    reviewer = st.text_input("Reviewer name", value="analyst")
    pending = q.pending()
    if not pending:
        st.success("No pending reviews.")
    for t in pending:
        with st.expander(f"{t['id']} · {', '.join(t['triggers'])} · due {t['sla_due_at']} UTC", expanded=False):
            st.markdown(f"**Question:** {t['question']}")
            for r in t["reasons"] or []:
                st.markdown(f"- {r}")
            st.markdown("**Draft answer**")
            st.json(t["draft"], expanded=False)
            st.markdown("**Evidence**")
            st.dataframe(pd.DataFrame(t["evidence"] or []), width="stretch", hide_index=True)
            with st.form(f"form_{t['id']}"):
                decision = st.radio("Decision", ["approved", "revised", "rejected"], horizontal=True)
                revised = st.text_area("Revised summary (for 'revised')", value=(t["draft"] or {}).get("summary", ""))
                note = st.text_input("Reviewer note")
                if st.form_submit_button("Submit decision"):
                    new = dict(t["draft"] or {}, summary=revised) if decision == "revised" else None
                    q.decide(t["id"], decision, reviewer, note, new)
                    st.success(f"{t['id']} {decision}.")
                    st.rerun()
    hist = [t for t in q.list() if t["status"] != "pending"]
    if hist:
        st.markdown("**History**")
        st.dataframe(pd.DataFrame([{k: t[k] for k in ("id", "status", "reviewer", "created_at", "decided_at", "note")}
                                   | {"triggers": ", ".join(t["triggers"] or [])} for t in hist]),
                     width="stretch", hide_index=True)
    if m["by_trigger"]:
        st.markdown("**Outcomes by trigger** (do the triggers work?)")
        st.dataframe(pd.DataFrame(m["by_trigger"]).T, width="stretch")

with tab_tools:
    resources()
    tools = [t["function"] for t in TOOL_SCHEMAS if t["function"]["name"] not in ("submit_answer", "ask_user")]
    name = st.selectbox("Tool", [t["name"] for t in tools])
    spec = next(t for t in tools if t["name"] == name)
    st.caption(spec["description"])
    args = {}
    for pname, p in spec["parameters"]["properties"].items():
        required = pname in spec["parameters"]["required"]
        label = f"{pname}{' *' if required else ''}"
        if p.get("enum"):
            v = st.selectbox(label, ([""] if not required else []) + p["enum"], key=f"{name}.{pname}")
        elif p.get("type") == "number":
            v = st.number_input(label, value=5.0, key=f"{name}.{pname}")
        elif p.get("type") == "boolean":
            v = st.checkbox(label, value=True, key=f"{name}.{pname}")
        elif pname == "sql":
            v = st.text_area(label, value="SELECT state, year, quarter, member_months FROM enrollment "
                                          "WHERE state = 'EV' ORDER BY year, quarter", key=f"{name}.{pname}")
        else:
            v = st.text_input(label, help=p.get("description"), key=f"{name}.{pname}")
        if v not in ("", None):
            args[pname] = v
    if st.button("Call tool"):
        st.json(new_toolbox().execute(name, json.dumps(args), WorkingMemory("tools explorer")), expanded=True)
    st.divider()
    st.markdown("**Long-term memory (knowledge store)**")
    ks = KnowledgeStore()
    st.dataframe(pd.DataFrame([f.__dict__ for f in ks.facts]), width="stretch", hide_index=True)
    keys = sorted({f.key for f in ks.facts})
    k = st.selectbox("Resolve a key", keys)
    st.json(ks.resolve(k).to_dict(), expanded=False)

with tab_eval:
    df = load_benchmarks()
    if df.empty:
        st.info("No eval results yet. Run: python eval/run_eval.py --provider deepseek")
    else:
        st.markdown("**Latest full-set run per mode**")
        latest = pd.DataFrame([latest_full(df, m) for m in df["mode"].unique() if latest_full(df, m) is not None])
        st.dataframe(latest, width="stretch", hide_index=True)
        c1, c2 = st.columns(2)
        c1.bar_chart(latest.set_index("mode")["pass_rate"], height=220)
        c2.bar_chart(latest.set_index("mode")["avg_s"], height=220)
        st.markdown("**All archived runs**")
        st.dataframe(df, width="stretch", hide_index=True)
        pick = st.selectbox("Inspect a run", df["run"].tolist()[::-1])
        data = json.loads((REPO_ROOT / "eval" / "results" / f"{pick}.json").read_text(encoding="utf-8"))
        if data["summary"].get("safety_metrics"):
            st.markdown("**Safety metrics and release gates**")
            st.json(data["summary"]["safety_metrics"], expanded=True)
        st.dataframe(pd.DataFrame([{"case": r["id"], "category": r["category"], "passed": r["passed"],
                                    "seconds": r.get("wall_seconds"),
                                    "failed checks": ", ".join(k for k, v in r["checks"].items() if not v)}
                                   for r in data["results"]]), width="stretch", hide_index=True)

with tab_mon:
    f = RUNS_DIR / "events.jsonl"
    if not f.exists():
        st.info("No runs logged yet. Ask a question in the Ask tab.")
    else:
        recs = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()[-200:] if l.strip()]
        st.dataframe(pd.DataFrame([{"time": r["ts"], "mode": r["config"].get("mode"), "question": r["question"][:90],
                                    "answer": r["answer_type"], "stop": r["stop_reason"],
                                    "monitor": (r.get("monitor") or {}).get("score"),
                                    "signals": ", ".join((r.get("monitor") or {}).get("signals", {})),
                                    "oversight": ", ".join((r.get("oversight") or {}).get("triggers", [])),
                                    "cost": r["usage"].get("cost_usd")} for r in recs[::-1]]),
                     width="stretch", hide_index=True)
        st.caption("Questions are PII-redacted before logging. Each record holds the full step list (tool, arguments, "
                   "errors, latency) for audit.")

with tab_about:
    st.markdown("""
**How it works:** ReAct agent → deterministic tools (SQL guard, statistics, coverage, policy timeline) + hybrid RAG
over real eCFR regulations → grounding verifier → trajectory monitor → oversight policy (withhold + review queue)
→ PII-redacted log.

**Modes:** *single* (default, 18/18 at ~10 s), *multi* (four-agent workflow, opt-in), *tot* (Tree-of-Thought
planner, opt-in), *no_retrieval* (ablation). The opt-in modes were benchmarked and kept off by default on evidence.

**Data:** utilization data and state documents are synthetic, for fictional states. Federal regulations are real
(eCFR snapshot). No PHI; identifiable inputs are blocked.

Docs: `docs/architecture.md` · `docs/design_log.md` · `docs/checkpoints/`.
""")
