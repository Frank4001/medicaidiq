"""Fill the CMU 7.1 final-report Word template with the MedicaidIQ report (preserves the template).

python scripts/fill_report.py  ->  deliverables/MedicaidIQ_Final_Capstone_Report.docx

Each of the template's 10 content controls ("Click or tap here to enter text.") is replaced, in order,
with the matching section below. Everything else in the template is left untouched.
"""

from __future__ import annotations

import re
import shutil
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "course/final_project/Self-study Capstone Checkpoint Activity 7.1_Final Capstone Report Planning Template.docx"
OUT = ROOT / "deliverables/MedicaidIQ_Final_Capstone_Report.docx"
AUTHOR = "Frank Sun"
TITLE = "MedicaidIQ: Final Capstone Report"
FIG = ROOT / "deliverables/figures/architecture.png"
REPO = "https://github.com/Frank4001/medicaidiq"
FONT = '<w:rFonts w:ascii="Open Sans" w:eastAsia="Open Sans" w:hAnsi="Open Sans" w:cs="Open Sans"/>'
BULLET_NUM = 90

# ----------------------------------------------------------------------------- XML builders


def _run(text: str, bold=False, italic=False, size=22, color="000000") -> str:
    # The template's document defaults make text bold, so non-bold runs must switch it off explicitly.
    b = "<w:b/><w:bCs/>" if bold else '<w:b w:val="0"/><w:bCs w:val="0"/>'
    return (f'<w:r><w:rPr>{FONT}{b}{"<w:i/>" if italic else ""}<w:color w:val="{color}"/>'
            f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr>'
            f'<w:t xml:space="preserve">{escape(text)}</w:t></w:r>')


def _runs(text: str, size=22) -> str:
    """**bold** segments supported."""
    out = []
    for i, part in enumerate(re.split(r"\*\*", text)):
        if part:
            out.append(_run(part, bold=(i % 2 == 1), size=size))
    return "".join(out)


def P(text: str, after=120, size=22, keep_next=False) -> str:
    return (f'<w:p><w:pPr><w:pStyle w:val="NormalWeb"/>{"<w:keepNext/>" if keep_next else ""}'
            f'<w:spacing w:before="0" w:beforeAutospacing="0" w:after="{after}" w:afterAutospacing="0"/>'
            f'<w:ind w:left="720"/></w:pPr>{_runs(text, size)}</w:p>')


def H(text: str) -> str:
    return (f'<w:p><w:pPr><w:pStyle w:val="NormalWeb"/><w:keepNext/><w:spacing w:before="160" w:beforeAutospacing="0" '
            f'w:after="60" w:afterAutospacing="0"/><w:ind w:left="720"/></w:pPr>'
            f'{_run(text, bold=True, size=22, color="A6192E")}</w:p>')


def B(text: str) -> str:
    return (f'<w:p><w:pPr><w:pStyle w:val="NormalWeb"/><w:numPr><w:ilvl w:val="0"/><w:numId w:val="{BULLET_NUM}"/>'
            f'</w:numPr><w:spacing w:before="0" w:beforeAutospacing="0" w:after="60" w:afterAutospacing="0"/>'
            f'<w:ind w:left="1080" w:hanging="360"/></w:pPr>{_runs(text)}</w:p>')


def T(rows: list[list[str]], widths: list[int], size=17) -> str:
    total = sum(widths)
    border = ''.join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>'
                     for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    out = [f'<w:tbl><w:tblPr><w:tblW w:w="{total}" w:type="dxa"/><w:tblInd w:w="720" w:type="dxa"/>'
           f'<w:tblBorders>{border}</w:tblBorders><w:tblLayout w:type="fixed"/>'
           f'<w:tblCellMar><w:top w:w="40" w:type="dxa"/><w:left w:w="80" w:type="dxa"/>'
           f'<w:bottom w:w="40" w:type="dxa"/><w:right w:w="80" w:type="dxa"/></w:tblCellMar></w:tblPr>'
           f'<w:tblGrid>{grid}</w:tblGrid>']
    for ri, row in enumerate(rows):
        hdr = ri == 0
        cells = []
        for w, txt in zip(widths, row):
            shade = '<w:shd w:val="clear" w:color="auto" w:fill="F2DCDB"/>' if hdr else ""
            cells.append(f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/>{shade}</w:tcPr>'
                         f'<w:p><w:pPr><w:spacing w:before="0" w:after="0"/></w:pPr>'
                         f'{_run(txt, bold=hdr, size=size) if hdr else _runs(txt, size)}</w:p></w:tc>')
        trpr = '<w:trPr><w:tblHeader/><w:cantSplit/></w:trPr>' if hdr else '<w:trPr><w:cantSplit/></w:trPr>'
        out.append(f"<w:tr>{trpr}{''.join(cells)}</w:tr>")
    out.append("</w:tbl>")
    return "".join(out) + P("", after=60)


def IMG(rid: str, cx: int, cy: int, caption: str) -> str:
    draw = (f'<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{cx}" cy="{cy}"/>'
            f'<wp:docPr id="901" name="MedicaidIQ architecture"/><wp:cNvGraphicFramePr>'
            f'<a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" noChangeAspect="1"/>'
            f'</wp:cNvGraphicFramePr><a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            f'<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:nvPicPr>'
            f'<pic:cNvPr id="0" name="architecture.png"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill>'
            f'<a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm>'
            f'<a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
            f'</pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>')
    return (f'<w:p><w:pPr><w:pStyle w:val="NormalWeb"/><w:keepNext/><w:spacing w:before="60" w:after="0"/>'
            f'<w:ind w:left="720"/></w:pPr>{draw}</w:p>'
            f'<w:p><w:pPr><w:pStyle w:val="NormalWeb"/><w:spacing w:before="0" w:after="160"/><w:ind w:left="720"/>'
            f'</w:pPr>{_run(caption, italic=True, size=18, color="595959")}</w:p>')


# ----------------------------------------------------------------------------- content
SECTIONS: list[list[str]] = []

# 1. Project title
SECTIONS.append([
    P("**MedicaidIQ: A Verifiable Agentic Research Assistant That Links Medicaid Drug-Policy Changes to Utilization "
      "Data**", after=60),
    P("Frank Sun · CMU Agentic AI Program, Capstone (Research Assistant track) · November 2026 · "
      f"Repository: {REPO}"),
])

# 2. Problem and user
SECTIONS.append([
    P("Medicaid policy and analytics professionals, program reviewers, and graduate researchers routinely ask "
      "questions such as \"Did GLP-1 prescribing in this state change after its prior-authorization requirement took "
      "effect?\" Answering correctly takes two kinds of evidence that rarely line up: **policy documents** (state plan "
      "amendments, provider bulletins, manuals, federal regulations, each with a jurisdiction, an authority level and "
      "an effective date that is often revised) and **utilization data** (drug-utilization records with reporting "
      "lags, suppressed small counts and changing enrollment)."),
    P("A stand-alone LLM fails this task in specific, predictable ways: it uses an announced or superseded date "
      "instead of the effective one, applies one state's rule to another, miscomputes percentages, treats incomplete "
      "recent quarters as real declines, reads rising spending as more care when enrollment grew, and implies a "
      "policy caused a change from a before/after comparison. These errors are invisible in fluent prose and costly "
      "in practice: analysts brief leadership, budget offices and legislators on such answers, and pharmacy policy "
      "(GLP-1 coverage is a current, high-cost example) moves real money."),
    P("MedicaidIQ addresses this with an agent that **looks up, computes and cites rather than recalls**: every number "
      "comes from a deterministic tool, every policy claim from a cited passage, and every answer states its "
      "confidence, caveats and what a human should verify, or it declines, asks, or escalates to a reviewer."),
])

# 3. System goal and scope
SECTIONS.append([
    P("**Goal.** Answer Medicaid drug-policy and utilization questions with answers that are correct, grounded, "
      "auditable and appropriately cautious, at low cost and latency."),
    H("Success criteria (measured on every release)"),
    B("Citation support ≥ 95% and numerical accuracy ≥ 95% (every finding cites evidence; headline numbers match the "
      "deterministic tool value)."),
    B("Zero missed mandatory escalations and zero successful prohibited actions (writes, identifiable data)."),
    B("Correct behavior on planted traps: superseded and conflicting sources, provisional quarters, enrollment-driven "
      "spending, out-of-scope states, prompt injection."),
    B("Interactive latency (target < 30 s per question) and cents-level cost."),
    H("Scope and boundaries"),
    B("**In scope:** outpatient drug policy with GLP-1 agonists as the focus (SGLT2 and insulin as controls); "
      "before/after analysis, data-quality checks, spending decomposition, and policy-text questions."),
    B("**Data:** a synthetic environment shaped like CMS State Drug Utilization Data for three fictional states "
      "(Northland, Eastvale, Southport) with planted effects and traps, plus real public federal regulations (eCFR) "
      "and data.medicaid.gov documentation. No PHI or restricted CMS data; identifiable inputs are refused."),
    B("**Out of scope:** claims adjudication, eligibility or payment decisions, and policy recommendations. The "
      "system reports findings with confidence; decision-sensitive questions are withheld for human review."),
    B("**Constraints:** read-only data access; public, synthetic or anonymized data only; a student budget (total "
      "LLM spend for the whole project ≈ $1.55)."),
])

# 4. Final system architecture
SECTIONS.append([
    IMG("rIdMiqArch", 4846320, 2344131, "Figure 1. MedicaidIQ architecture: guardrails → ReAct agent with "
        "deterministic tools and hybrid RAG → verifier → oversight, with monitoring, logging and evaluation."),
    P("MedicaidIQ is a **single ReAct agent** (reason → act → observe) using native function calling over a "
      "provider-agnostic client, wrapped in layered safety controls. One pipeline serves the command line, the "
      "evaluation harness and the demo console, so every path gets identical safeguards."),
    B("**Reasoning loop:** each turn writes a one-line thought and calls one tool; observations drive the next step. "
      "Stops on a verified structured answer, a clarifying question, a guardrail, step/time limits, or provider "
      "failure (an honest \"cannot answer\"). Final output uses a JSON schema (answer type, findings with evidence ids, "
      "confidence, caveats, items for human review)."),
    B("**Tools (each tied to an LLM limitation):** policy timeline, hybrid policy search, data coverage, read-only "
      "SQL behind a four-layer guard, pre/post analysis with bootstrap 95% CI and a significance × materiality "
      "verdict, spending decomposition (enrollment / use per member / price), and an exact calculator."),
    B("**Memory:** short-term working memory (slots, numbered evidence E1…En, failed attempts) re-injected each "
      "step; long-term knowledge store with provenance, supersession, a source-authority hierarchy (regulation > SPA > "
      "federal guidance > manual > bulletin), conflict states, staleness review and human approval of agent "
      "interpretations."),
    B("**Retrieval:** paragraph-level chunks (regulations grouped by (a), (b)…), BM25 + bge-small-en-v1.5 embeddings "
      "fused by reciprocal rank fusion, MMR reranking, adjacent-paragraph expansion, and relevance/scope gates; "
      "answers cite chunk ids such as [42-CFR-430.20(b)]."),
    B("**Advanced reasoning and coordination (opt-in):** a Tree-of-Thought planner (beam search over analysis plans, "
      "tool-verified rubric) and a four-agent workflow (deterministic controller, planner, policy researcher, claims "
      "analyst, reviewer, shared evidence store, failover). Both were built, tested and benchmarked; evidence kept "
      "the single agent as the default (Section 7)."),
    B("**Guardrails, logging, evaluation, human intervention:** input screens (PII, write intent, injection), "
      "retrieved-text injection screen, grounding and calibration verifier, trajectory monitor, PII-redacted run "
      "log, review queue with SLAs, and an eval harness with release gates (Section 8). An MCP server exposes the "
      "tools, a policy resource and plan-tree records to any MCP host; a Streamlit console provides the demo, "
      "side-by-side mode comparison and the reviewer interface."),
])

# 5. Design evolution
SECTIONS.append([
    P("Each module added one capability; each decision was logged with its reason (68 entries in docs/design_log.md) "
      "and, from Module 2 on, measured on the eval set."),
    T([["Module", "What was added or changed", "Why / evidence"],
       ["1 · Framing", "Problem, users, failure modes; LLM never does arithmetic; abstention as a valid outcome; "
        "MCP-shaped tools", "Lab 1: tokenization errors, hallucination on missing info; grader asked about claims "
        "lag, significance vs. materiality, conflicting sources → designed coverage checks, materiality verdicts, "
        "authority hierarchy"],
       ["2 · ReAct, memory, tools", "Working ReAct agent, 9 tools, working + long-term memory, verifier, MCP server, "
        "eval harness; scope narrowed to GLP-1 drug policy; DuckDB instead of Postgres; DeepSeek chosen over "
        "Claude/Kimi/local", "Grader asked how stored knowledge is superseded → supersession, authority conflicts, "
        "review triggers implemented. LLM validated live: DeepSeek 9/9 at 9 s vs local Qwen3-8B 8/9 at 69 s"],
       ["3 · RAG", "Hybrid retrieval over real eCFR regulations; paragraph chunking; MMR; adjacency; scope gate; "
        "write-intent guardrail", "Ablation: retrieval cases 1/5 without → 5/5 with. Found and fixed a truncated-answer "
        "bug and an out-of-scope gap (Illinois scored 0.681 on federal text)"],
       ["4 · Tree-of-Thought", "Beam-search planner (b = 2, depth 3, 10-point rubric, prune < 7); kept opt-in; "
        "CrewAI/LangChain evaluated, not adopted", "Linear 7/7 at 14 s vs ToT 6/7 at 83 s; fixed empty generations, "
        "ungrounded proposals (whole beam pruned) and a 49-minute hang"],
       ["5 · Multi-agent", "Four-agent workflow with deterministic controller and failover; kept opt-in; "
        "fine-tuning rejected", "Single 18/18 at 9.8 s vs multi-agent best 17/18 at 49 s after three iterations; each "
        "handoff was a failure point. Answered \"why four agents\", reviewer dual role, controller failure"],
       ["6 · Safety & oversight", "PII and injection screens, calibration check, monitor, review queue with SLAs, "
        "release gates, demo console", "24/24 with all release gates; an unnecessary escalation caught by the metric "
        "and fixed. Answered \"why 95%\", reviewer metrics, how long answers can wait"]],
      [1450, 3900, 4000]),
    P("**Most important refinements.** (1) Moving the deterministic hard decisions (dates, statistics, coverage, "
      "permissions) into code made a low-cost model reliable. (2) Building the evaluation in Module 2, before most "
      "features, made every later decision measurable: two sophisticated techniques (ToT, multi-agent) were "
      "implemented and then kept opt-in because measurement showed they added latency and cost without accuracy. "
      "(3) Reading failure traces found real agent bugs (a truncated answer accepted unverified, a state-name handoff bug, a 49-minute network hang) and 8 grader blind spots that scores alone would have hidden."),
])

# 6. Implementation overview
SECTIONS.append([
    T([["Layer", "Technology", "How it supports the design"],
       ["Language & packaging", "Python 3.11, pyproject (pip install -e .)", "One-command setup for reviewers"],
       ["LLM", "DeepSeek deepseek-flash via the OpenAI-compatible SDK; Qwen (fallback) and local Qwen3-8B "
        "(llama.cpp, Vulkan, AMD RX 6700S) supported", "Provider-agnostic client: switching models is a config change; "
        "JSON mode, per-call timeouts, token and cost metering"],
       ["Data", "DuckDB (read-only) with SDUD-shaped synthetic tables", "Zero-setup SQL; read-only connection is a "
        "native guardrail"],
       ["Retrieval", "fastembed (ONNX) bge-small-en-v1.5 + BM25, RRF, MMR; cached NumPy index", "Local, free "
        "embeddings; exact codes and citations via BM25"],
       ["Public sources", "eCFR API (10 Title-42 sections, version 2026-11-18); data.medicaid.gov metastore and "
        "datastore APIs", "Official, machine-readable, version-pinned, SHA-256 provenance"],
       ["Interoperability", "MCP SDK 2.x (MCPServer): 7 tools, policy and schema resources, plan-tree resource, a "
        "prompt", "Any MCP host (Claude Desktop, VS Code) can use the tools"],
       ["Interface", "CLI (medicaidiq ask/ui); Streamlit console", "Demo, side-by-side comparison, reviewer "
        "workflow, tools explorer, eval dashboard"],
       ["Quality", "pytest (118 offline tests with a scripted LLM); eval harness (24 live cases)", "Deterministic "
        "regression tests at $0; live evidence for every claim"]],
      [1900, 3700, 3750]),
    P("**Cost.** All non-LLM components are free. Total DeepSeek spend for the entire project, including 270 graded "
      "evaluation runs across all configurations, was about $1.55 (≈ $0.003 per question)."),
])

# 7. Evaluation and results
SECTIONS.append([
    P("**Method.** A synthetic environment with **planted ground truth** (known effect sizes, a superseded bulletin, a "
      "lower-authority conflicting manual, an equal-authority conflict, provisional quarters, a missing quarter, "
      "suppressed counts, enrollment-driven spending, an injected document) makes answers objectively gradable. 24 "
      "eval cases cover core analysis, every trap, retrieval, abstention, clarification, consistency (paraphrase), "
      "safety and escalation. Deterministic graders check answer type, required tools, the effective date actually "
      "used, numbers against the tool value, citations, disclosures, absence of fabricated numbers, verification and "
      "escalation behavior. Ablations compare configurations on identical cases; failures are read before acting."),
    T([["Final results (DeepSeek deepseek-flash, live)", "Result"],
       ["Eval cases passed", "**24 / 24**"],
       ["Citation support / numerical accuracy", "**100% / 100%** (gates ≥ 95%)"],
       ["Missed / unnecessary mandatory escalations", "**0 / 0** (escalation rate 12.5%)"],
       ["Prohibited-action successes · guardrail blocks", "**0** · 2 (PII, write request)"],
       ["Latency average / p95 · cost per question", "10.3 s / 20.1 s · $0.0028"]],
      [5600, 3750]),
    T([["Ablation (same cases)", "Accuracy", "Avg time", "Cost / question", "Decision"],
       ["Retrieval off → on (5 RAG cases)", "1/5 → 5/5", "–", "–", "RAG required for policy text"],
       ["Single agent vs + Tree-of-Thought (7 analysis cases)", "7/7 vs 6/7", "14 s vs 83 s", "$0.004 vs $0.019",
        "ToT opt-in"],
       ["Single agent vs four-agent (18 cases)", "18/18 vs 17/18", "9.8 s vs 49.4 s", "$0.0025 vs $0.0137",
        "Multi-agent opt-in"],
       ["DeepSeek vs local Qwen3-8B (9 cases)", "9/9 vs 8/9", "9 s vs 69 s", "$0.002 vs $0", "DeepSeek primary"]],
      [3150, 1450, 1450, 1650, 1650]),
    P("**Interpretation.** The system meets every release gate. Its strength is reliability on the hard parts (dates, "
      "data quality, statistics, jurisdiction), which deterministic tools settle; a cheap model only chooses and "
      "explains. ToT and multi-agent coordination worked once their failure modes were fixed, but did not beat the "
      "simpler design on this task. **Limitations of the evaluation:** 24 cases on synthetic data; keyword-based "
      "checks needed synonym fixes (8 grader blind spots found by trace review); run-to-run model variation exists "
      "(two cases failed once and passed after prompt fixes)."),
])

# 8. Safety and reliability
SECTIONS.append([
    B("**Input guardrails (before any model call):** identifiable information (SSN, member ID, date of birth, phone, "
      "email, address) and write/destructive requests are refused deterministically at $0; injection-style text in "
      "questions is flagged and treated as data."),
    B("**Tool and data guardrails:** read-only DuckDB connection + SELECT-only validator + table allowlist + "
      "row/time limits; role-isolated tools in multi-agent mode; retrieval scope gates (out-of-scope state, low "
      "relevance). Retrieved text is evidence, never instructions: sentences with embedded instructions are removed "
      "and flagged (a planted injected FAQ is in the test set)."),
    B("**Output checks:** every number must appear in a tool result and every citation must exist; causal "
      "overclaims and overconfident wording are sent back for rewrite; unparseable answers are rejected; up to two "
      "correction retries, then disclosure."),
    B("**Monitoring and fallback:** a trajectory monitor scores each run (write attempts, injections, out-of-scope "
      "tools, repeated failures, unverified releases, PII); a PII-redacted structured log records every step; "
      "per-call and per-question time limits; provider errors and controller failures fall back to an honest "
      "\"cannot answer\" or to the single agent."),
    B("**Human oversight:** four mandatory triggers withhold the conclusion until a reviewer approves, revises or "
      "rejects it in the review queue: a case decision (eligibility, payment, coverage, compliance; SLA 24 h), an "
      "equal-authority source conflict the answer depends on (72 h), an unresolved policy date (72 h), or a causal "
      "question (72 h methodological review). Recommended triggers (unverified, monitor flag, low confidence) release "
      "the answer and queue a 7-day review. Expired tickets are never auto-released (fail closed). Reviewer "
      "approval, revision and rejection rates are tracked per trigger to tune the triggers."),
    P("**Trade-off.** Routine research stays autonomous and fast; only narrow, consequential conditions require a "
      "human. Deterministic guardrails cost microseconds and are auditable, at the price of missing novel phrasings, "
      "which monitoring and review are designed to catch."),
])

# 9. Limitations and next steps
SECTIONS.append([
    H("Current limitations"),
    B("Utilization data and state policy documents are synthetic; only federal regulations are real. The evaluation "
      "set (24 cases) is small, and keyword-based graders are brittle."),
    B("Regex-based PII and injection screens catch common patterns only; the heuristic monitor is calibrated on a "
      "small benign set."),
    B("Pre/post analysis shows association, not causation (no comparison-state design), and reimbursement is "
      "pre-rebate."),
    B("The review queue has not seen real reviewer traffic, so trigger tuning is untested in practice."),
    H("Realistic next steps"),
    B("**Real data, one state:** an Illinois GLP-1 slice is already shown to be feasible: CMS SDUD and enrollment APIs "
      "work (968 Illinois GLP-1 rows, 2022 to 2026; 95% managed care; incomplete 2026 Q1), and the state agency's "
      "preferred drug list is published as a spreadsheet. About 4–5 days of work."),
    B("Expand the eval to 40+ cases with structured or LLM-judge grading for wording; add difference-in-differences "
      "with comparison states for causal questions."),
    B("Validate the free local model (Qwen3-8B) on the full suite with tuned settings as an offline backup; add an "
      "ML-based PII detector and an LLM trajectory monitor."),
    B("Pilot with analysts: sample routine answers for review alongside all escalations, and turn every failure into "
      "a regression test before expanding to new states or drug classes."),
])

# 10. Public GitHub repository
SECTIONS.append([
    P(f"**{REPO}** (public; release tag v1.0)", after=80),
    B("**README:** problem, architecture diagram, 5-command setup, usage (CLI and demo console), evaluation commands, "
      "data and privacy notes."),
    B("**Code:** src/medicaidiq/ (agent, tools, memory, retrieval, planner, multi-agent, guardrails, oversight, "
      "monitor, pipeline, MCP server, evaluation); app/streamlit_app.py; 118 tests in tests/."),
    B("**Data and artifacts:** synthetic data and policy documents with a README of planted ground truth; real eCFR "
      "snapshots with provenance; eval cases and every archived eval run in eval/results/ (including ToT plan trees)."),
    B("**Design record:** docs/ holds the architecture, a 68-entry design log, weekly checkpoint write-ups, LLM "
      "selection and cost analysis, the requirements traceability matrix and the Illinois real-data feasibility study."),
    B("**Run it:** pip install -e \".[dev,ui]\" → medicaidiq build-data → pytest -q (no API key) → set "
      "DEEPSEEK_API_KEY → medicaidiq ask \"…\" or medicaidiq ui."),
])

# ----------------------------------------------------------------------------- assemble


def main() -> int:
    assert len(SECTIONS) == 10
    work = OUT.parent / "_report_build"
    shutil.rmtree(work, ignore_errors=True)
    with zipfile.ZipFile(TEMPLATE) as z:
        z.extractall(work)
    doc_path = work / "word" / "document.xml"
    from lxml import etree
    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    NS = {"w": W, "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
          "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"}
    tree = etree.fromstring(doc_path.read_bytes())
    sdts = list(tree.iter(f"{{{W}}}sdt"))
    assert len(sdts) == 10, len(sdts)

    def blocks(content: list[str]):
        wrapper = ("<root " + " ".join(f'xmlns:{k}="{v}"' for k, v in NS.items()) + ">"
                   + "".join(content) + "</root>")
        return list(etree.fromstring(wrapper.encode("utf-8")))

    for sdt, content in zip(sdts, SECTIONS):
        parent = sdt.getparent()
        if etree.QName(parent).localname == "p":
            # Inline control inside the instruction paragraph: remove the placeholder box and insert the
            # answer as block content right after the paragraph (paragraphs/tables can't live inside a <w:p>).
            parent.remove(sdt)
            # The template put two line breaks before the placeholder box; without the box they leave a gap.
            while len(parent) and etree.QName(parent[-1]).localname == "r" and \
                    parent[-1].find(f"{{{W}}}br") is not None and parent[-1].find(f"{{{W}}}t") is None:
                parent.remove(parent[-1])
            anchor = parent
            for el in blocks(content):
                anchor.addnext(el)
                anchor = el
        else:
            # Block-level control: replace its content in place.
            for tag in sdt.iter(f"{{{W}}}showingPlcHdr"):
                tag.getparent().remove(tag)
            sc = sdt.find(f"{{{W}}}sdtContent")
            for child in list(sc):
                sc.remove(child)
            for el in blocks(content):
                sc.append(el)
    doc_path.write_bytes(etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True))

    # bullet numbering definition
    num_path = work / "word" / "numbering.xml"
    num = num_path.read_text(encoding="utf-8")
    abstract = (f'<w:abstractNum w:abstractNumId="{BULLET_NUM}"><w:multiLevelType w:val="singleLevel"/>'
                f'<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/>'
                f'<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="1080" w:hanging="360"/></w:pPr>'
                f'<w:rPr>{FONT}</w:rPr></w:lvl></w:abstractNum>')
    first_num = num.find("<w:num ")
    num = num[:first_num] + abstract + num[first_num:]
    num = num.replace("</w:numbering>", f'<w:num w:numId="{BULLET_NUM}"><w:abstractNumId w:val="{BULLET_NUM}"/>'
                                       f'</w:num></w:numbering>')
    num_path.write_text(num, encoding="utf-8")

    # architecture figure
    shutil.copy(FIG, work / "word" / "media" / "medicaidiq_architecture.png")
    rels_path = work / "word" / "_rels" / "document.xml.rels"
    rels = rels_path.read_text(encoding="utf-8").replace(
        "</Relationships>", '<Relationship Id="rIdMiqArch" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                            'relationships/image" Target="media/medicaidiq_architecture.png"/></Relationships>')
    rels_path.write_text(rels, encoding="utf-8")

    # Document properties: the template carries its own authors; the report is the student's.
    core = work / "docProps" / "core.xml"
    if core.exists():
        c = core.read_text(encoding="utf-8")
        for tag, value in (("dc:creator", AUTHOR), ("cp:lastModifiedBy", AUTHOR), ("dc:title", TITLE)):
            c = re.sub(rf"<{tag}>[^<]*</{tag}>|<{tag}/>", f"<{tag}>{value}</{tag}>", c)
            if f"<{tag}>" not in c:
                c = c.replace("</cp:coreProperties>", f"<{tag}>{value}</{tag}></cp:coreProperties>")
        core.write_text(c, encoding="utf-8")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        OUT.unlink()
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(work.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(work).as_posix())
    shutil.rmtree(work)
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
