"""Command-line interface.

  medicaidiq build-data                       # synthetic data + policy corpus + long-term memory
  medicaidiq ask "question" [--provider deepseek] [--interactive] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from .paths import KNOWLEDGE_PATH, RUNS_DIR


def build_data() -> None:
    from . import synth
    from .memory import KnowledgeStore
    from .retrieval import load_corpus

    db = synth.build()
    if KNOWLEDGE_PATH.exists():
        KNOWLEDGE_PATH.unlink()
    ks = KnowledgeStore()
    # Ingest in publication order: later documents can supersede earlier ones.
    for doc in sorted(load_corpus(), key=lambda d: d.meta["published"]):
        ks.ingest_document(doc.meta)
    ks.save()
    print(f"Database: {db}\nLong-term memory: {KNOWLEDGE_PATH} ({len(ks.facts)} facts)")
    try:
        from .retrieval import PolicySearch
        s = PolicySearch()
        print(f"Retrieval index: {len(s.chunks)} chunks, mode={s.mode}")
    except Exception as exc:  # retrieval is optional for the data build
        print(f"Retrieval index not built ({exc}); search falls back to keyword mode.")


def make_agent(provider: str | None = None, model: str | None = None, interactive: bool = False,
               retrieval: bool = True):
    from .agent import MedicaidIQAgent
    from .db import ReadOnlyDB
    from .llm import ChatLLM
    from .tools import Toolbox

    clarify = (lambda q: input(f"\nMedicaidIQ asks: {q}\n> ").strip() or None) if interactive else None
    return MedicaidIQAgent(ChatLLM(provider=provider, model=model), Toolbox(ReadOnlyDB()), clarify=clarify,
                           retrieval=retrieval)


def render(result) -> str:
    a = result.answer
    lines = [f"\n=== {a.get('answer_type', '?').upper()} (confidence: {a.get('confidence')}) ===",
             a.get("summary", "")]
    for f in a.get("findings", []):
        lines.append(f"  - {f['statement']}  [{', '.join(f.get('evidence_ids', []))}]")
    if a.get("caveats"):
        lines.append("Caveats:")
        lines += [f"  - {c}" for c in a["caveats"]]
    if a.get("human_review"):
        lines.append("For human review:")
        lines += [f"  - {c}" for c in a["human_review"]]
    lines.append("\nEvidence trail:")
    lines += [f"  {e['id']} [{e['tool']}] {e['summary']}" for e in result.evidence]
    u = result.usage
    lines.append(f"\n{result.steps} steps · stop={result.stop_reason} · verified={result.verified} · "
                 f"{u['prompt_tokens']}+{u['completion_tokens']} tokens · ${u['cost_usd']:.4f} · "
                 f"{u['llm_seconds']}s LLM time · {u['model']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    p = argparse.ArgumentParser(prog="medicaidiq")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build-data", help="Generate synthetic data, policy corpus, long-term memory, retrieval index")
    sub.add_parser("fetch-public", help="Re-download public eCFR + data.medicaid.gov documents (needs network)")
    ask = sub.add_parser("ask", help="Ask a question")
    ask.add_argument("question")
    ask.add_argument("--provider", default=None, help="deepseek | qwen | kimi | local")
    ask.add_argument("--model", default=None)
    ask.add_argument("--interactive", action="store_true", help="Answer clarifying questions")
    ask.add_argument("--json", action="store_true", help="Print the full result as JSON")
    ask.add_argument("--no-retrieval", action="store_true", help="Ablation: disable search_policy (no RAG)")
    args = p.parse_args(argv)

    if args.cmd == "build-data":
        build_data()
        return 0
    if args.cmd == "fetch-public":
        from .ingest_public import fetch_all
        for path in fetch_all():
            print(f"saved {path}")
        return 0

    try:
        agent = make_agent(args.provider, args.model, args.interactive, retrieval=not args.no_retrieval)
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        print(f"Setup error: {exc}", file=sys.stderr)
        return 2
    result = agent.run(args.question)
    RUNS_DIR.mkdir(exist_ok=True)
    out = RUNS_DIR / f"run_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(result.to_dict(), indent=2, default=str), encoding="utf-8")
    print(json.dumps(result.to_dict(), indent=2, default=str) if args.json else render(result))
    print(f"\nFull trace saved to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
