# Project Cost Estimate

*Version 2 (Week 2, 2026-11-20). Based on measured prompt sizes from the working agent and the LLM decision in [`llm_selection.md`](llm_selection.md). Version 1 (Week 1) assumed Claude and 46k tokens per question; that estimate is kept in git history.*

## Bottom line

**Expected total cash cost: about $14 in LLM API fees (budget cap $25).** Everything else is $0.

| Item | Choice | Cost |
|---|---|---|
| LLM API (primary) | DeepSeek `deepseek-flash`, ~1,475 agent runs | ≈ $14 incl. 25% contingency |
| LLM API (fallback) | Qwen `qwen-plus`, used only if needed for the demo | ≈ $0–5 |
| Local model | Qwen3-8B on own GPU (offline tests) | ≈ $1 electricity |
| Evaluation grading | Deterministic checks, no LLM judge | $0 |
| Data | Synthetic now; public CMS data later (Week 3+) | $0 |
| Database / vector search | DuckDB embedded; local embeddings | $0 |
| Hosting | Public GitHub; demo runs locally and is recorded | $0 |
| Report and slides | Word / PowerPoint (existing license) | $0 |
| Recording | OBS Studio or PowerPoint recorder | $0 |

## Assumptions

1. **Tokens per question:** 20k input and 1.5k output. The measured 5-step run is about 14k input, and the margin covers verification retries and longer questions. About 2.1k of every call (system prompt plus tool schemas) is a byte-stable, cacheable prefix.
2. **Volume:** about 1,475 agent runs from Week 2 to Week 7 (600 development, 520 evaluation, 50 validation, 50 demo, 255 buffer).
3. **Prices:** as researched 2026-11-20 (third-party trackers; verify before paying). The budget uses DeepSeek's *peak*, no-cache price as the worst case.
4. **Reasoning mode off by default.** Turning on a "thinking" mode can multiply output tokens 3–5× (for example, Qwen thinking output costs $4/M). We'll enable it only where the eval shows a gain.

## Controls

- Limits on agent steps (10) and verification retries (2), so loops can't run away.
- Tool observations are truncated to 3,500 characters; aggregated results go to the model, never raw tables.
- A fixed system prompt and tool list, to maximize cache hits.
- Token usage and cost are logged for every run (`runs/`, `eval/results/`), so this estimate can be re-checked against actual spend weekly.
- Spending is prepaid (DeepSeek top-up), so overspending is impossible by construction.
