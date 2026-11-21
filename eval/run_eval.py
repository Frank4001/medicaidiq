"""Run the eval set against one or more LLM providers.

Examples:
  python eval/run_eval.py --provider deepseek
  python eval/run_eval.py --provider local --model qwen3-8b --ids prepost_ev clarify
  python eval/run_eval.py --provider deepseek --provider qwen      # side-by-side comparison

Results: eval/results/<provider>_<timestamp>.json (full traces) + printed summary.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from medicaidiq.evaluation import run_eval  # noqa: E402
from medicaidiq.llm import ChatLLM  # noqa: E402

RESULTS = Path(__file__).resolve().parent / "results"


def main() -> int:
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--provider", action="append", required=True)
    p.add_argument("--model", default=None)
    p.add_argument("--base-url", default=None)
    p.add_argument("--ids", nargs="*", default=None)
    p.add_argument("--max-steps", type=int, default=10)
    p.add_argument("--no-retrieval", action="store_true", help="Ablation: agent without search_policy")
    p.add_argument("--category", default=None, help="Only cases whose category starts with this (e.g. rag)")
    p.add_argument("--plan", action="store_true", help="Enable the Tree-of-Thought planner")
    args = p.parse_args()

    RESULTS.mkdir(exist_ok=True)
    summaries = []
    for provider in args.provider:
        factory = lambda: ChatLLM(provider=provider, model=args.model, base_url=args.base_url)
        label = (factory().label + ("+no-retrieval" if args.no_retrieval else "")
                 + ("+tot" if args.plan else ""))
        print(f"\n### {label}")
        run = run_eval(factory, label, ids=args.ids, max_steps=args.max_steps,
                       retrieval=not args.no_retrieval, category=args.category, planning=args.plan)
        summary = run.summary()
        summaries.append(summary)
        out = RESULTS / f"{label.replace(':', '_').replace('/', '-')}_{datetime.now():%Y%m%d_%H%M%S}.json"
        out.write_text(json.dumps({"summary": summary, "results": run.results}, indent=2, default=str),
                       encoding="utf-8")
        print(json.dumps(summary, indent=2))
        print(f"saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
