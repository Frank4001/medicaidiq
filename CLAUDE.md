# CLAUDE.md: MedicaidIQ capstone

Project memory for this workspace: what we're building, how we work, and what we've decided.
**This file overrides `~/CLAUDE.md` (the personal PARA filing system) inside this folder.** Here we edit files in place and rely on git for history. No `_v2` copies.

## What we're building

**MedicaidIQ**, the capstone for the CMU Agentic AI program (Research Assistant option). A ReAct agent that answers Medicaid **drug-policy** questions (GLP-1 focus) by resolving policy effective dates from long-term memory, checking data coverage, running deterministic statistics on a read-only DuckDB, and returning a structured, verified answer with evidence IDs, confidence, and caveats, or clarifying or abstaining.
Goal: **100% on every rubric criterion.** Track coverage in `docs/requirements_traceability.md`.

## Final deliverables (end of Week 7; build them only then)

1. **Report**: filled directly into the Word template `course/final_project/...Report Planning Template.docx` (10 sections).
2. **Slides**: PowerPoint, following the 7-part outline; 8–10 minute recorded video. Optional 90-second pitch.
3. **Public GitHub repo `medicaidiq`**: README, code, sample I/O, eval artifacts, run instructions.

## Workspace layout

| Path | Purpose | In git? |
|---|---|---|
| `course/weekN/{assignment,labs}`, `course/final_project/` | Private course materials (prompts, rubrics, submissions, feedback, labs, templates) | **No** |
| `src/medicaidiq/` | Agent package (see README layout) | Yes |
| `tests/` | 49 offline tests (scripted LLM; no key needed) | Yes |
| `eval/` | `cases.jsonl`, `run_eval.py`, `results/` (live results go here) | Yes |
| `data/synthetic/` | Generated CSVs + policy docs (fictional states); `.duckdb` and `knowledge_store.json` are rebuilt | CSVs and docs yes |
| `docs/` | checkpoints, `design_log.md`, `architecture.md`, `llm_selection.md`, `cost_estimate.md`, `requirements_traceability.md` | Yes |
| `.tools/` | Local llama.cpp (Vulkan) + Qwen3-8B GGUF | No |
| `.venv/` | uv-created Python 3.11 env: `.venv/Scripts/python.exe` | No |

New weekly material arrives as `WeekN/` at the root; move it into `course/weekN/`. (An empty locked `Week2/` folder may linger; it's gitignored.)

## Weekly loop

1. Read the labs, homework prompt, rubric, submission, and feedback.
2. For each lab concept, decide whether it's relevant and how it applies; turn the feedback into concrete design or code changes.
3. Write `docs/checkpoints/weekN_*.md`; add `D<week>.<n>` entries to `docs/design_log.md`.
4. Implement, add tests, run `pytest -q`, and run the eval if a key is available.
5. Update `docs/requirements_traceability.md`, the README status line, and this file. Commit.

## Commands

```bash
.venv/Scripts/python.exe -m medicaidiq.cli build-data     # rebuild synthetic env + memory
.venv/Scripts/python.exe -m pytest -q                     # all tests
.venv/Scripts/python.exe eval/run_eval.py --provider deepseek
.venv/Scripts/python.exe -m medicaidiq.cli ask "..." --provider deepseek
```

## Key decisions and assumptions (details in docs/design_log.md)

- **Scope (D2.1):** outpatient drug policy; GLP-1 is the focus, with SGLT2/insulin as controls; 3 **fictional** states; synthetic SDUD-shaped data with planted effects and traps. Behavioral health and Synthea dropped. Real public SDUD comes in Week 3+.
- **LLM (D2.3):** DeepSeek `deepseek-flash` primary, Qwen `qwen-plus` fallback, local Qwen3-8B for offline smoke tests; Kimi rejected. **Assumption:** prices from third-party trackers (2026-11-20). **Pending live validation.**
- **Cost:** about $14 LLM total (cap $25); everything else $0 (`docs/cost_estimate.md`).
- **DB (D2.4):** DuckDB read-only, replacing Postgres + pgvector, for one-step reproducibility.
- **Loop (D2.5):** native function calling, no LangChain; `submit_answer` JSON schema; verifier with 2 retries.
- **Memory (D2.7/D2.8):** working memory with evidence IDs; knowledge store with supersession, authority hierarchy, conflict state, staleness review, and human approval of agent interpretations.
- **Eval (D2.11):** deterministic graders, no LLM judge; decision rule = cheapest provider with ≥ 8/9 pass, no malformed tool calls, average < 30 s.
- **Repo (D2.12):** name `medicaidiq`; committed locally; **not pushed** (gh is not authenticated).

## Working rules

- Data: public, synthetic, or anonymized only. Never PHI or restricted CMS data. Only synthetic or public text goes to LLM providers.
- Never publish course materials, grader names, or feedback text; paraphrase feedback themes.
- Secrets live in `.env` (gitignored) only.
- The LLM never does arithmetic (D1.2). Never claim an eval result we haven't measured.
- Don't push or create the GitHub repo without the user's go-ahead.
- **MCP SDK is 2.x:** `from mcp.server.mcpserver import MCPServer` (FastMCP was renamed); tests use `mcp.Client(server)`. The course labs use the 1.x API.
- Running downloaded binaries (llama-server) was blocked by the auto-mode permission classifier. Ask the user to start it rather than working around the block.

## Concepts learned → where used

| Week | Concept | Applied as |
|---|---|---|
| 1 | Native reasoning vs. hand-written CoT | D1.7: reasoning effort set by provider config; off by default for cost |
| 1 | CoT can be unfaithful | D1.8: audit trail = tool trace + evidence IDs |
| 1 | Hallucination on missing info | D1.4: `cannot_answer` path + `abstain_state` eval case |
| 1 | Brittleness | D1.5: `prepost_ev_paraphrase` case + consistency metric |
| 1 | Quality × cost × latency | D1.6: eval logs cost and latency; LLM selection doc |
| 1 | Tokenization / numeric errors | D1.2: calculator + verifier; D1.3: exact-match retrieval |
| 1 | MCP primitives, elicitation, roots/scopes | D2.10 MCP server; `ask_user`; read-only least privilege |
| 2 | ReAct Thought/Action/Observation, max steps | `agent.py` with native tool calls, scratchpad, stop conditions |
| 2 | Regex parsing is fragile → API tool calling | D2.5 |
| 2 | Local vs. API model trade-offs | `docs/llm_selection.md` (RX 6700S: 8B is the ceiling) |
| 2 | Custom MCP server with domain tools | `mcp_server.py` (7 tools, 2 resources, 1 prompt) |
| 2 | Short- vs. long-term memory | `WorkingMemory` / `KnowledgeStore` |

## Lessons learned

- A hand-computed expected value in a test was wrong (3,206,132 vs. 3,205,932). The calculator was right. It's a good demo anecdote for D1.2.
- Measured prompt size (~14k tokens over a 5-step run) was 3× smaller than the Week 1 guess. Measure before budgeting.
- **First live eval (local Qwen3-8B):** 7/9 raw, 8/9 after a grader keyword fix; 69 s average per question. Every analysis and trap case passed; the safety refusal (`unsafe_write`) failed in two different ways across runs. Lesson: keyword graders need synonyms, and small models are inconsistent on refusals. Next step: a deterministic write-intent pre-check (don't rely on the LLM alone).
- Southport shows a small, significant but immaterial dip (−3.5%) from noise. It's a useful live example of the significance × materiality quadrant.

## Open decisions / blockers

- [x] **Live LLM validation done (2026-11-20):** DeepSeek 9/9, 9 s average, $0.0023 per question → confirmed primary. Local Qwen3-8B 8/9, 69 s. The user has a $5 prepaid DeepSeek balance (key in `.env`; never read or print it). Projected total ≈ $3.40.
- [ ] Qwen fallback not yet validated (no key); optional before the demo.
- [ ] Add a deterministic write-intent pre-check (DeepSeek refused correctly, but don't rely on the LLM alone).
- [ ] **GitHub:** user runs `gh auth login`; then create the public `medicaidiq` repo and push.
- [ ] Week 3: hybrid retrieval (embeddings), chunking with section references, real public SDUD extract.
- [ ] Expand the eval to about 40 cases; add a formal success-criteria list (A3).
