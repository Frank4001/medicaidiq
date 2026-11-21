# CLAUDE.md: MedicaidIQ capstone

Project memory for this workspace: what we're building, how we work, and what we've decided.
**This file overrides `~/CLAUDE.md` (the personal PARA filing system) inside this folder.** Here we edit files in place and rely on git for history. No `_v2` copies.

## What we're building

**MedicaidIQ**, the capstone for the CMU Agentic AI program (Research Assistant option). A ReAct agent that answers Medicaid **drug-policy** questions (GLP-1 focus) by resolving policy effective dates from long-term memory, retrieving policy text with hybrid RAG (real eCFR regulations + synthetic state documents), running deterministic statistics on a read-only DuckDB, and returning a structured, verified answer with evidence IDs, paragraph-level citations, confidence, and caveats, or clarifying or abstaining.
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
| `tests/` | 118 offline tests (scripted LLM; no key needed; the embedding model downloads once to `.tools/fastembed`) | Yes |
| `eval/` | `cases.jsonl`, `run_eval.py`, `results/` (live results go here) | Yes |
| `data/synthetic/` | Generated CSVs + policy docs (fictional states); `.duckdb` and `knowledge_store.json` are rebuilt | CSVs and docs yes |
| `data/public/` | Real eCFR regulations + data.medicaid.gov SDUD description (JSON snapshots with URL, version, sha256) | Yes |
| `data/index/` | Cached embedding matrix (rebuilt from a hash of the corpus) | No |
| `docs/` | checkpoints, `design_log.md`, `architecture.md`, `llm_selection.md`, `cost_estimate.md`, `requirements_traceability.md` | Yes |
| `.tools/` | Local llama.cpp (Vulkan) + Qwen3-8B GGUF | No |
| `.venv/` | uv-created Python 3.11 env: `.venv/Scripts/python.exe` | No |

New weekly material arrives as `WeekN/` at the root; move it into `course/weekN/`. (Empty locked `WeekN/` folders may linger; they're gitignored.)

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
.venv/Scripts/python.exe eval/run_eval.py --provider deepseek                       # 14 cases, ~3 cents
.venv/Scripts/python.exe eval/run_eval.py --provider deepseek --category rag --no-retrieval   # RAG ablation
.venv/Scripts/python.exe -m medicaidiq.cli fetch-public                            # re-download eCFR corpus
.venv/Scripts/python.exe -m medicaidiq.cli ask "..." --provider deepseek
```

## Key decisions and assumptions (details in docs/design_log.md)

- **Scope (D2.1):** outpatient drug policy; GLP-1 is the focus, with SGLT2/insulin as controls; 3 **fictional** states; synthetic SDUD-shaped data with planted effects and traps. Behavioral health and Synthea dropped. Real public SDUD comes in Week 3+.
- **LLM (D2.3):** DeepSeek `deepseek-flash` primary, Qwen `qwen-plus` fallback, local Qwen3-8B for offline smoke tests; Kimi rejected. **Assumption:** prices from third-party trackers (2026-11-20). **Pending live validation.**
- **Cost:** about $14 LLM total (cap $25); everything else $0 (`docs/cost_estimate.md`).
- **DB (D2.4):** DuckDB read-only, replacing Postgres + pgvector, for one-step reproducibility.
- **Loop (D2.5):** native function calling, no LangChain; `submit_answer` JSON schema; verifier with 2 retries.
- **Memory (D2.7/D2.8):** working memory with evidence IDs; knowledge store with supersession, authority hierarchy, conflict state, staleness review, and human approval of agent interpretations.
- **Retrieval (D3.1–D3.9):** RAG only for policy text; eCFR + data.medicaid.gov (Medicaid.gov returns 403; don't get around it); paragraph chunks; BM25 + bge-small → RRF → MMR λ 0.7 → top 4 + adjacent paragraph (≤ 6); `low_relevance` < 0.68; `out_of_scope` when a requested state has no docs; one search per state for comparisons.
- **ToT (D4.1–D4.8):** `planner.py`, opt-in via `--plan`; beam 2, at most 3 children, depth 3, at most 15 nodes; rubric = scope/evidence/feasibility (tool-verified) + interpretation (LLM critic); prune below 7; ground facts after depth 1; budget 30 LLM / 20 tool / 60 s. CrewAI/LangChain NOT adopted (deviation from Checkpoint 4.1, justified); MCP serves branch records as `plan://{id}`; final benchmark trees committed in `eval/results/plan_trees/`.
- **Multi-agent (D5.1–D5.7):** `multiagent.py`, opt-in via `--multi`: deterministic controller; planner (scope + write), policy researcher, claims analyst, reviewer (drafts only); isolated tools; shared thread-safe evidence; ≤ 1 revision; failover to the single agent. Benchmark: v1 12/18, v2 13/18, v3 17/18 vs. single 18/18 → single agent stays the default. Open: data-evidence handoff still summarized (v3 `coverage_sp`).
- **Fine-tuning (D5.8):** rejected; tools + RAG instead.
- **Safety and oversight (D6.1–D6.12):** `guardrails.py` (PII, injection, calibration, redaction), `oversight.py` (4 mandatory + 4 recommended triggers, SQLite review queue, SLA 24/72/168 h, fail closed, per-trigger reviewer metrics), `monitor.py` (suspicion score, threshold 40; `runs/events.jsonl`), `pipeline.py` (single entry point). Release gates: citation ≥ 95%, numeric ≥ 95%, 0 missed escalations, 0 prohibited actions. Eval tickets go to `runs/review_queue_eval.sqlite`.
- **Demo UI (D6.11):** `app/streamlit_app.py`, launched with `medicaidiq ui` (extra `[ui]`). Ask tab compares modes side by side with the benchmark trade-off.
- **Guardrails (D3.10–D3.11, D4.6):** deterministic write-intent refusal before the LLM; unparseable final answers rejected; output limit 4,096 tokens; 240 s per question, 90 s per call; provider errors become `cannot_answer`.
- **Eval (D2.11):** deterministic graders, no LLM judge; decision rule = cheapest provider with ≥ 8/9 pass, no malformed tool calls, average < 30 s.
- **Repo (D2.12):** public at https://github.com/Frank4001/medicaidiq (published 2026-11-20). Commits use the GitHub no-reply email (set in the repo-local git config).

## Working rules

- Data: public, synthetic, or anonymized only. Never PHI or restricted CMS data. Only synthetic or public text goes to LLM providers.
- Never publish course materials, grader names, or feedback text; paraphrase feedback themes.
- Secrets live in `.env` (gitignored) only.
- The LLM never does arithmetic (D1.2). Never claim an eval result we haven't measured.
- Pushing routine weekly commits to origin is fine; ask before anything destructive (force-push, history rewrite, visibility change).
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
| 3 | RAG = parametric + non-parametric memory | `search_policy` tool; retrieval only where text matters (D3.1) |
| 3 | DPR: title + passage, dense similarity | bge-small with title-prefixed paragraph chunks (D3.3) |
| 3 | RAG-Sequence vs. RAG-Token | Our agent synthesizes across passages in one answer (token-style mixing) while citing each passage; no model training needed |
| 3 | Long context vs. RAG vs. MemGPT | RAG for documents; MemGPT-style paging ≈ our working-memory scratchpad + tools loading evidence on demand |
| 3 | MMR relevance/diversity | MMR rerank λ 0.7 (D3.5) |
| 4 | ToT: thought decomposition, propose/value, BFS/beam | `planner.py` (D4.2–D4.3) |
| 4 | Generation quality is the bottleneck | Ground before generating (D4.4) |
| 4 | When is ToT worth the compute? | Benchmark: not here (D4.1) |
| 5 | Agent count, roles, coordination (sequential/graph/hybrid), communication (one-way/two-way) | `multiagent.py` hybrid graph; one-way handoffs + one two-way review (D5.2–D5.4) |
| 5 | Conditional routing, retry cap, failover, benchmark protocols | Controller failover, ≤ 1 revision, 3-run benchmark (D5.1, D5.5) |
| 5 | Fine-tuning: DAPT, LoRA, instruction tuning | Considered and rejected (D5.8) |
| 4 | Harness = prompt, tools, loop, context, permissions, environment | Mapped in the Week 4 checkpoint §8; time limits added (D4.6) |

## Lessons learned

- A hand-computed expected value in a test was wrong (3,206,132 vs. 3,205,932). The calculator was right. It's a good demo anecdote for D1.2.
- Measured prompt size (~14k tokens over a 5-step run) was 3× smaller than the Week 1 guess. Measure before budgeting.
- **First live eval (local Qwen3-8B):** 7/9 raw, 8/9 after a grader keyword fix; 69 s average per question. Every analysis and trap case passed; the safety refusal (`unsafe_write`) failed in two different ways across runs. Lesson: keyword graders need synonyms, and small models are inconsistent on refusals. Next step: a deterministic write-intent pre-check (don't rely on the LLM alone).
- **Week 3 live runs:** 12/14 → 14/14. Found and fixed a real bug (a cut-off JSON answer was accepted unverified), a retrieval scope gap (Illinois at 0.681 similarity), and 2 more grader false negatives (CFR numbers counted as claims; "opposite" missing from contrast words). Keyword grading is brittle; consider a structured rubric or LLM judge in the evaluation module.
- **Week 4 live ToT runs:** v1 4/7 (empty generations, `finish=length`); v2 4/7 (ungrounded date guesses, whole beam pruned; a 49-minute network hang); v3 6/7 after grounding and time limits. Linear 7/7 at 14 s vs. ToT 83 s, so ToT is opt-in only. Lesson: ToT only helps where deterministic tools can't already settle the choice.
- **Week 5 live multi-agent runs:** v1 12/18 (lossy handoffs + 2 grader blind spots), v2 13/18 (state-name truncation bug at the scope handoff + 2 grader misses), v3 17/18 at 5x latency. Lesson: each handoff is an interface that can lose or corrupt information; one agent with all the tools has none.
- **Week 6 live runs:** 23/24 (1 unnecessary escalation: conflict trigger too broad) → 22/24 (model slips: other-state facts in a single-agent refusal; a prose refusal) → **24/24, all release gates**. Testing also caught: injection neutralization left the payload readable; the calibration regex flagged a correct hedge.
- The agent found a corpus gap itself (42 CFR 447.256 cited but missing) and declined to state it. We added it. That's good demo material for grounded behavior.
- Southport shows a small, significant but immaterial dip (−3.5%) from noise. It's a useful live example of the significance × materiality quadrant.

## Open decisions / blockers

- [x] **Live LLM validation done (2026-11-20):** DeepSeek 9/9, 9 s average, $0.0023 per question → confirmed primary. Local Qwen3-8B 8/9, 69 s. The user has a $5 prepaid DeepSeek balance (key in `.env`; never read or print it). Projected total ≈ $3.40.
- [ ] Qwen fallback not yet validated (no key); optional before the demo.
- [x] Deterministic write-intent guardrail (D3.10).
- [x] **GitHub:** published at https://github.com/Frank4001/medicaidiq.
- [x] Week 3: hybrid RAG over eCFR + state documents; ablation 1/5 → 5/5; DeepSeek 14/14 (2026-11-20). DeepSeek spend so far ≈ $0.18 (≈ $0.65 after the Week 4 benchmarks).
- [ ] **Real Illinois data (stretch, Weeks 5–6):** feasibility confirmed 2026-11-20, see `docs/real_data_feasibility_illinois.md`. SDUD + enrollment APIs work (968 IL GLP-1 rows 2022–2026; 95% MCO; 2026Q1 incomplete; 23% suppressed). HFS site allowed by robots.txt (be polite, ≥ 2 s between requests); PDL available as XLSX. GLP-1 PA criteria not yet located. Estimated 4–5 days. Never infer IL policy dates from claims; they must come from HFS docs plus human review.
- [x] Week 4: ToT planner built and benchmarked (opt-in); 18/18 default eval.
- [x] Week 5: four-agent workflow built, 3 benchmark runs (best 17/18 vs. single 18/18); single agent stays the default; fine-tuning rejected.
- [ ] Week 5 grader asked "why four agents": answered in docs/checkpoints/week5_multi_agent.md §5.
- [x] Week 6: guardrails, oversight, monitor, release gates, demo console; 24/24 with all gates passing; the 3 grader questions answered (§3, §4).
- [ ] Demo rehearsal: run `medicaidiq ui`, walk the HITL flow (causal question → withheld → approve in the Review tab) and the single-vs-multi comparison.
- [ ] Week 4 checkpoint has no grader feedback yet; check when it arrives.
- [x] **Method playbook DONE (2026-11-20):** user-level Claude Code skill `agent-builder` at `~/.claude/skills/agent-builder/` (SKILL.md: phases 0-7 with eval moved to phase 2, gates, checklists, anti-patterns; templates/: CLAUDE.md, design_log, requirements_traceability, checkpoint, eval_cases, llm_selection). Update it with Week 6-7 lessons.
- [ ] **REUSABLE CODE TEMPLATE, post-submission task (requested 2026-11-20), ~1 day.** Extract the domain-independent
  ~70% of this repo into (a) a GitHub template repo `agentic-project-template` (minimal working agent + toy domain,
  docs skeleton: CLAUDE.md / design_log / requirements_traceability / checkpoint / cost / LLM-selection templates,
  ReAct core with time limits, failover and verifier, provider-agnostic LLM client + scripted test LLM, tool-registry
  pattern, read-only SQL guard, MCP wrapper, hybrid RAG, opt-in ToT and multi-agent modules, eval harness with
  ablation flags and a comparison runner, planted-effects data-generator pattern) and (b) a Claude Code skill or
  playbook capturing the METHOD (weekly loop, ground truth first, code settles hard decisions, benchmark before
  adding complexity, inspect traces before trusting scores). Improve on lessons: structured graders instead of
  keyword lists; a fixed benchmark protocol. User may apply it to an "HTC project" where they want to train
  their own agent (scope to be clarified).
- [ ] **LOCAL MODEL PLAN, deferred at the user's request (2026-11-20).** Circle back AFTER the DeepSeek
  benchmarks and AFTER Week 6. Plan: (1) the user starts llama-server (`.tools\llama\llama-server.exe -m
  .tools\models\Qwen3-8B-Q4_K_M.gguf --device Vulkan1 -ngl 99 -c 16384 --jinja --reasoning-format deepseek
  --port 8080`); (2) run the full 18-case eval with `--provider local` on stock settings; (3) re-run with tuned
  settings: Qwen3 thinking OFF for tool calls (roughly halves latency) and a quantized KV cache for 32k context
  (current prompts average ~12k against a 16k limit). Verify the exact llama.cpp flags for build b12584 first;
  (4) add a DeepSeek vs. local-8B (stock/tuned) row to the report's model comparison. Positioning: DeepSeek stays
  primary; local 8B = free offline backup. No model upgrade possible on this laptop (8 GB VRAM / 16 GB RAM:
  8B is the ceiling). ToT and multi-agent on local are impractical (~3-6 min per question).
- [x] **Week 7 final deliverables built (2026-11-20)**, private in `deliverables/` (gitignored): Word report (9 pages, from `scripts/fill_report.py`), a 12-slide PowerPoint (pptxgenjs build in `deliverables/_deck/build_deck.js`, timed speaker notes ~9:55), PDFs of both, and `MedicaidIQ_Recording_Script.md` (video script, click-by-click demo, 90-second pitch). Figures: `scripts/make_figures.py`. Repo tagged v1.0.
- [ ] **USER:** record the 8–10 minute video (and optional pitch) and submit the report, slides, video and repo link.
- [ ] Expand the eval to about 40 cases (now 18); add a formal success-criteria list (A3); replace brittle keyword checks.
