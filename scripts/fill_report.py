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
    P("**MedicaidIQ: A Verifiable Research Agent That Links Medicaid Drug-Policy Changes to Utilization Data**",
      after=60),
    P("Frank Sun · CMU Agentic AI Program, Capstone (Research Assistant track) · November 2026 · "
      f"Repository: {REPO}"),
])

# 2. Problem and user
SECTIONS.append([
    P("Medicaid policy analysts, program reviewers and researchers ask questions such as \"Did GLP-1 prescribing in "
      "this state change after its prior-authorization requirement took effect?\" A correct answer needs two kinds "
      "of evidence that rarely line up: **policy documents**, each with a jurisdiction, an authority level and an "
      "effective date that is often revised, and **utilization data** with reporting lags, suppressed small counts "
      "and shifting enrollment. The answers reach budget offices and leadership, and GLP-1 coverage is an expensive, "
      "current example, so errors are costly."),
    P("A stand-alone language model fails this task in predictable ways: it uses a superseded date, applies one "
      "state's rule to another, miscomputes percentages, reads incomplete quarters as declines, mistakes "
      "enrollment-driven spending for more care, and implies causation. MedicaidIQ is built so that every number "
      "comes from a deterministic tool and every policy claim from a cited passage; otherwise it declines, asks a "
      "clarifying question, or escalates to a reviewer."),
])

# 3. System goal and scope
SECTIONS.append([
    P("**Goal.** Correct, grounded and appropriately cautious answers to Medicaid drug-policy questions, at "
      "interactive speed and cents-level cost."),
    H("Success criteria (checked on every release)"),
    B("Citation support and numerical accuracy ≥ 95%; zero missed mandatory escalations; zero prohibited actions."),
    B("Correct behavior on every planted trap; under 30 s per question."),
    H("Scope"),
    B("**In scope:** outpatient drug policy, GLP-1 agonists as the focus with SGLT2 and insulin as controls; "
      "before/after analysis, data-quality checks, spending decomposition, policy-text questions."),
    B("**Data:** synthetic CMS-shaped utilization data for three fictional states with planted effects and traps, "
      "plus real federal regulations (eCFR). No PHI; identifiable inputs are refused; access is read-only."),
    B("**Out of scope:** eligibility, payment or coverage decisions and policy recommendations; such questions are "
      "withheld for human review."),
])

# 4. Final system architecture
SECTIONS.append([
    IMG("rIdMiqArch", 4846320, 2344131, "Figure 1. Guardrails → ReAct agent with deterministic tools and hybrid "
        "retrieval → verifier → oversight, with monitoring, logging and evaluation."),
    P("A **single ReAct agent** uses native function calling through a provider-agnostic client. One pipeline serves "
      "the command line, the evaluation harness and the demo console, so every path gets the same safeguards."),
    B("**Tools:** policy timeline, hybrid policy search, data coverage, read-only SQL behind a four-layer guard, "
      "pre/post analysis (bootstrap 95% CI, significance × materiality verdict), spending decomposition "
      "(enrollment / use / price) and an exact calculator. The model selects tools; it never does arithmetic."),
    B("**Memory:** working memory with numbered evidence (E1…En) re-injected each step; a long-term knowledge store "
      "with provenance, supersession, a source-authority hierarchy, conflict states and human approval of "
      "agent-derived facts."),
    B("**Retrieval:** paragraph chunks, BM25 + bge-small embeddings fused by reciprocal rank, MMR reranking, "
      "adjacent-paragraph expansion and scope/relevance gates; answers cite chunk ids such as [42-CFR-430.20(b)]."),
    B("**Opt-in modes:** a Tree-of-Thought planner and a four-agent workflow (planner, policy researcher, claims "
      "analyst, reviewer) were built and benchmarked; measurement kept the single agent as the default (Section 7)."),
    B("**Safety and interfaces:** input and retrieved-text screens, a grounding and calibration verifier, a "
      "trajectory monitor, a review queue (Section 8); an MCP server and a Streamlit console for demo and review."),
])

# 5. Design evolution
SECTIONS.append([
    P("Each module added one capability. All 68 decisions are recorded with their reasons in docs/design_log.md, and "
      "from Module 2 on each was measured on the evaluation set."),
    T([["Module", "Added or changed", "Evidence"],
       ["1 · Framing", "Users, failure modes, abstention as a valid outcome, no model arithmetic",
        "Feedback on claims lag and conflicting sources → coverage checks, authority hierarchy"],
       ["2 · ReAct, memory, tools", "Working agent, 9 tools, both memories, verifier, MCP server, eval harness; "
        "DuckDB; DeepSeek selected", "DeepSeek 9/9 at 9 s vs local Qwen3-8B 8/9 at 69 s"],
       ["3 · Retrieval", "Hybrid search over real eCFR text; write-intent guardrail",
        "Retrieval cases 1/5 without → 5/5 with; truncated-answer bug fixed"],
       ["4 · Tree-of-Thought", "Beam-search planner, tool-verified rubric; kept opt-in",
        "7/7 at 14 s (linear) vs 6/7 at 83 s (ToT)"],
       ["5 · Multi-agent", "Four agents, deterministic controller, failover; kept opt-in; fine-tuning rejected",
        "18/18 at 9.8 s (single) vs best 17/18 at 49 s"],
       ["6 · Safety & oversight", "PII and injection screens, calibration, monitor, review queue, release gates",
        "24/24 with every gate passing"]],
      [1700, 3900, 3750]),
    P("**Key lessons.** Moving the hard decisions (dates, statistics, coverage, permissions) into code made a "
      "low-cost model reliable. Building the evaluation early made every later choice measurable, including two "
      "techniques kept opt-in. Reading failure traces found bugs a score would hide, such as an unverified truncated "
      "answer and a 49-minute network hang, and eight grader errors."),
])

# 6. Implementation overview
SECTIONS.append([
    T([["Layer", "Technology"],
       ["Language", "Python 3.11 package (pip install -e .)"],
       ["Model", "DeepSeek deepseek-flash through an OpenAI-compatible API client; Qwen and a local Qwen3-8B "
        "(llama.cpp) are drop-in alternatives"],
       ["Data", "DuckDB opened read-only"],
       ["Retrieval", "BM25 + bge-small-en-v1.5 (fastembed, local); cached index"],
       ["Sources", "eCFR API (10 Title 42 sections) and data.medicaid.gov, with version dates and SHA-256 hashes"],
       ["Interfaces", "CLI, Streamlit console, MCP server (7 tools, 3 resources)"],
       ["Quality", "118 offline tests with a scripted model; 24-case live evaluation"]],
      [1900, 7450]),
    P("**Cost.** Everything except the model API is free. Total model spend for the project, including 270 graded "
      "evaluation runs, was about $1.55."),
])

# 7. Evaluation and results
SECTIONS.append([
    P("**Method.** The synthetic environment has **planted ground truth**: known effect sizes, a superseded bulletin, "
      "conflicting sources, provisional and missing quarters, suppressed counts, enrollment-driven spending and an "
      "injected document. The 24 cases cover analysis, every trap, retrieval, abstention, clarification, paraphrase "
      "consistency, safety and escalation. Graders are deterministic: they check the date actually used, numbers "
      "against the tool's value, citations, fabricated numbers and escalation behavior."),
    T([["Final run (DeepSeek, live)", "Result"],
       ["Cases passed", "**24 / 24**"],
       ["Citation support / numerical accuracy", "**100% / 100%** (gates ≥ 95%)"],
       ["Missed / unnecessary escalations · prohibited actions", "**0 / 0 · 0**"],
       ["Average / p95 latency · cost per question", "10.3 s / 20.1 s · $0.0028"]],
      [5600, 3750]),
    T([["Comparison (same cases)", "Accuracy", "Avg time", "Decision"],
       ["Retrieval off vs on (5 cases)", "1/5 vs 5/5", "–", "Retrieval required"],
       ["Single vs Tree-of-Thought (7 cases)", "7/7 vs 6/7", "14 s vs 83 s", "ToT opt-in"],
       ["Single vs four-agent (18 cases)", "18/18 vs 17/18", "9.8 s vs 49.4 s", "Multi-agent opt-in"],
       ["DeepSeek vs local Qwen3-8B (9 cases)", "9/9 vs 8/9", "9 s vs 69 s", "DeepSeek primary"]],
      [3700, 1850, 1900, 1900]),
    P("**What the results support.** The system meets every release gate, and its reliability comes from tools "
      "settling the hard parts. Tree-of-Thought and multi-agent coordination worked after fixes but did not beat the "
      "simpler design here. The evaluation is small (24 cases), runs on synthetic data, and uses keyword checks that "
      "needed eight fixes."),
])

# 8. Safety and reliability
SECTIONS.append([
    B("**Before the model:** identifiable information and write requests are refused deterministically, at zero "
      "cost; injection-style text is treated as data."),
    B("**Tools and data:** read-only database plus a SELECT-only validator, table allowlist and row/time limits. "
      "Retrieved text is evidence, never instructions: sentences with embedded instructions are removed and flagged."),
    B("**Output checks:** every number must appear in a tool result and every citation must exist; causal "
      "overclaims are sent back for rewriting; after two failed retries the answer discloses the problem."),
    B("**Monitoring:** a trajectory score and a PII-redacted log for every run; time limits; provider errors become "
      "an honest \"cannot answer\"."),
    B("**Human oversight:** four triggers withhold the conclusion until a reviewer approves, revises or rejects it: "
      "a case decision (24 h), a source conflict the answer depends on, an unresolved policy date, or a causal "
      "question (72 h each). Expired tickets are never released. Outcomes are tracked per trigger to tune them."),
])

# 9. Limitations and next steps
SECTIONS.append([
    H("Limitations"),
    B("State data and documents are synthetic; the evaluation is small and its keyword checks are brittle."),
    B("PII and injection screens are pattern-based; before/after results show association only, and amounts are "
      "pre-rebate; the review queue has not seen real reviewers."),
    H("Next steps"),
    B("Add a real Illinois GLP-1 slice: the CMS utilization and enrollment APIs and the state's published drug list "
      "were confirmed usable."),
    B("Grow the evaluation to 40+ cases with structured grading; add comparison-state designs for causal questions; "
      "pilot with analysts and turn every failure into a regression test."),
])

# 10. Public GitHub repository
SECTIONS.append([
    P(f"**{REPO}** (public; release tag v1.0)", after=80),
    B("**README:** problem, architecture, setup, usage, evaluation commands, data notes."),
    B("**Code:** src/medicaidiq/, the console in app/, and 118 tests in tests/."),
    B("**Artifacts:** synthetic data with its planted ground truth, eCFR snapshots, eval cases and every archived run "
      "in eval/results/."),
    B("**Design record:** docs/ holds the architecture, design log, weekly checkpoints, model selection and cost."),
    B("**Run it:** pip install -e \".[dev,ui]\" → medicaidiq build-data → pytest -q → set DEEPSEEK_API_KEY → "
      "medicaidiq ui."),
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
