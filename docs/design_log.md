# Design Log

A record, week by week, of design decisions: what we decided, why, and what it replaced. This is the source for the report's **"Design evolution across the program"** section and the presentation's **"Key design decisions"** slide.

Format: `D<week>.<n>`, the decision, why, and its status (active / superseded by …).

---

## Week 1: LLM foundations and MCP (problem framing)

| ID | Decision | Why | Status |
|---|---|---|---|
| D1.1 | Build **MedicaidIQ**, a Research Assistant that links Medicaid policy evidence to utilization and spending data | A real multi-step problem: retrieval, computation, and judgment can't happen in one prompt | Active |
| D1.2 | **The LLM never does arithmetic**; every number comes from SQL or Python tools, and a final check confirms each number in the answer appears in a tool output | Lab 1.1 tokenization (the "9.11 vs 9.9" failure) | Active |
| D1.3 | **Hybrid retrieval** (keyword/BM25 plus vector search) with metadata filters for state and effective date | Codes and document IDs (NDC, HCPCS, SPA numbers) tokenize poorly, so exact match is needed | Active |
| D1.4 | **Abstention and clarification are valid outcomes**, tested by dedicated eval cases | Lab 1.1 Test 5 (hallucination on missing information); MCP elicitation | Active |
| D1.5 | Eval set includes **paraphrase groups** to measure consistency | Lab 1.1 Test 6 (brittleness) | Active |
| D1.6 | **Model-agnostic eval harness** that logs quality, cost, and latency for each run | Lab 1.1 "quality × cost × latency" model ladder | Active |
| D1.7 | Use the model's **native adaptive reasoning**; vary reasoning effort by step | Lab 1.1: CoT has been absorbed into the model; the skill is knowing when to spend compute | Active |
| D1.8 | The **audit trail is the tool trace** (queries, sources, computations), not the model's private reasoning | Lab 1.1: CoT monitoring can be unfaithful | Active |
| D1.9 | Tool layer is **MCP-shaped**: tools, resources by URI, prompt templates. A FastMCP server wrapper is a stretch goal | Lab 1.2; revisit in Week 2 (MCP server lab) | Active |
| D1.10 | **Least privilege**: read-only database role, allowlisted schemas, SELECT-only validator, row limits and timeouts | Lab 1.2 roots and scopes | Active |
| D1.11 | **Data-completeness and coverage checks** (claims lag, SDUD suppression, missing state-periods) run before any comparison | Grader feedback F1 | Active |
| D1.12 | Report **effect size + CI + materiality threshold**; classify findings into four significance/materiality quadrants | Grader feedback F2 | Active |
| D1.13 | **Source authority hierarchy**; latest *effective* version wins; conflicts are surfaced with both citations | Grader feedback F3 | Active |
| D1.14 | Answers report findings with confidence. No policy recommendations | Grader feedback F4; scope boundary | Active |
| D1.15 | Only public or synthetic data; restricted CMS data (T-MSIS/TAF) out of scope | Course data policy; no data use agreement | Active |

**Implementation status after Week 2:** implemented and tested: D1.2, D1.4, D1.5 (1 paraphrase pair), D1.6, D1.8, D1.9, D1.10, D1.11, D1.12, D1.13, D1.14. Partial: D1.3 (BM25 only; vectors in Week 3). D1.7 depends on the provider (reasoning mode off by default for cost; see D2.3).

---

## Week 2: Reasoning loops, memory, tools

| ID | Decision | Why | Status |
|---|---|---|---|
| D2.1 | **MVP scope:** outpatient drug policy, with GLP-1 agonists as the focus and SGLT2/insulin as control classes; 3 states; pre/post, coverage, and spending-decomposition questions. Behavioral health and Synthea claims are dropped | Public SDUD has drug data, so real data can be added later. Known planted effects give a gradeable eval. Fits the remaining 5 weeks | Active (narrows D1.1) |
| D2.2 | **Synthetic test environment with fictional states** (Northland, Eastvale, Southport), using an SDUD-shaped schema and planted effects and traps | Ground truth for evaluation, and no fake numbers attached to real states | Active |
| D2.3 | **LLM:** DeepSeek `deepseek-flash` primary, Qwen `qwen-plus` fallback, local Qwen3-8B for offline smoke tests. Provider-agnostic OpenAI-compatible client | Lowest cost that meets the tool-calling, structured-output, and context needs (`docs/llm_selection.md`) | Active, pending live validation |
| D2.4 | **DuckDB (read-only)** instead of PostgreSQL + pgvector | `pip install` and run, no server: reviewers can reproduce the project in one step. Read-only mode is a native guardrail | Active (supersedes the Week 1 Postgres plan) |
| D2.5 | **Native function calling** instead of the lab's regex ReAct parsing; no LangChain | Schema-validated arguments; a transparent loop; portable across providers | Active |
| D2.6 | Final output through a **`submit_answer` JSON schema** + **grounding verifier** with up to 2 correction retries | Structured, checkable answers; catches invented numbers and bad citations | Active |
| D2.7 | **Working memory**: slots, numbered evidence (E1…), and failed attempts, re-injected as a scratchpad each step | Keeps the analysis consistent across steps; answers cite evidence IDs | Active |
| D2.8 | **Long-term knowledge store** with supersession, an authority hierarchy, conflict states, a staleness review, and human approval for agent interpretations | Grader feedback on Checkpoint 2.1 | Active |
| D2.9 | A **policy starting mid-quarter excludes that mixed quarter** from both periods | Quarterly data can't separate pre and post within a quarter | Active |
| D2.10 | **MCP server** implemented (MCP SDK 2.x `MCPServer`) exposing tools, resources, and a prompt | Interoperability; completes the D1.9 stretch goal | Active |
| D2.11 | **Deterministic eval graders** (no LLM judge) | Free, reproducible, and no judge bias; possible because ground truth is known | Active |
| D2.12 | Public repo **`medicaidiq`** on GitHub; commits use a GitHub no-reply address | A public repo is required; the no-reply address keeps a personal email out of the history | Active |

---

## Week 3: Retrieval (RAG) and data grounding

| ID | Decision | Why | Status |
|---|---|---|---|
| D3.1 | **Retrieval is a tool, used for policy text only.** Numbers come from SQL and statistics; authoritative dates from the knowledge store | Each kind of question gets the grounding that fits it; retrieval never feeds arithmetic | Active |
| D3.2 | **External source: eCFR API + data.medicaid.gov** (11 official documents, version-pinned, with provenance hashes). Medicaid.gov pages not used | Official and machine-accessible; Medicaid.gov returns HTTP 403 to automated clients and we don't get around that | Active (supersedes the prototype's Medicaid.gov telehealth pages) |
| D3.3 | **Paragraph-level chunking** that follows regulation structure ((a), (b)…, with sub-items kept under their parent, split at ~180 words); title prepended for embedding | Keeps rules with their conditions; exact citations; DPR title+passage (Lab 3.1) | Active |
| D3.4 | **Hybrid retrieval:** BM25 + bge-small-en-v1.5 dense, combined by Reciprocal Rank Fusion | Completes D1.3: meaning plus exact codes and citations | Active (completes D1.3) |
| D3.5 | **MMR rerank (λ = 0.7), top 4, + adjacent paragraph, ≤ 6 passages** | Lab 3.2 diversity; the prototype's finding that an exception sits in the next paragraph | Active |
| D3.6 | **Relevance and scope gate**: `low_relevance` below a best similarity of 0.68; `out_of_scope` when a requested state has no documents, regardless of score | Live run: an Illinois query scored 0.681 on federal text, so a similarity score alone is not enough | Active |
| D3.7 | **Local embeddings (fastembed/ONNX) + cached NumPy index**, no vector database | Free, offline, 57 chunks; a vector database adds setup with no benefit at this size | Active; revisit if the corpus grows |
| D3.8 | **Paragraph-level citations** (`[EV-MAN-2024-PH#p1]`, `[42-CFR-430.20(b)]`) required in findings | Every claim can be checked at paragraph level | Active |
| D3.9 | **Multi-state questions: one search per state, every rule attributed to its jurisdiction** | Grader question on states with different rules; prevents cross-state contamination | Active |
| D3.10 | **Deterministic write-intent guardrail** before any LLM call | Local 8B failed safety refusals; don't depend on the model | Active (closes the Week 2 item) |
| D3.11 | **Reject unparseable final answers**; output limit raised to 4,096 tokens | Live bug: a cut-off JSON answer was accepted unverified | Active |
| D3.12 | **Retrieval ablation is part of the eval** (`--no-retrieval`) | Shows retrieval's effect: 1/5 → 5/5 | Active |

---

## Week 4: Tree-of-Thought and agentic harnesses

| ID | Decision | Why | Status |
|---|---|---|---|
| D4.1 | **ToT planner for analysis-plan selection, as an opt-in tool** (`plan_analysis`, `--plan`); linear ReAct stays the default | Benchmark: linear 7/7 at 14 s vs. ToT 6/7 at 83 s (5.9x latency, 5.4x cost). The traps ToT searches over are already settled by deterministic tools (D1.2, D1.11, D2.8) | Active |
| D4.2 | **Thought = structured JSON plan**; depth 1 measure, 2 comparison design, 3 executable steps; at most 3 children; beam width 2; at most 15 nodes | Shallow, constraint-heavy tree (like the paper's Game of 24); beam keeps one alternative alive without explosion | Active |
| D4.3 | **10-point rubric, mostly deterministic**: scope, evidence, and feasibility verified by tool calls; LLM critic only for interpretation; prune below 7; hard failures override | Lab 4.1: code settles what it can; the critic judges what code can't | Active |
| D4.4 | **Ground before generating**: fetch the verified timeline and coverage after depth 1 | Live v2 failure: ungrounded date guesses led to the whole beam being pruned | Active |
| D4.5 | **Retry empty or unparseable planner replies once**; larger output budget; concise-output instructions | Live v1 failure: `finish=length`, empty content | Active |
| D4.6 | **Hard time limits**: per-call timeout from the remaining budget (planner); 240 s per question and 90 s per call (agent); provider errors become an honest `cannot_answer` | Live: a 49-minute hang on a connection error | Active |
| D4.7 | **CrewAI and LangChain evaluated, not adopted**; roles implemented natively; **MCP used for branch state** (`plan://{plan_id}`) | Simplest scaffolding (harness lab / Building Effective Agents); no duplicate loop; full inspectability | Active (deviation from the Checkpoint 4.1 proposal) |
| D4.8 | A policy question with a single-quarter design gets no window-feasibility credit | One quarter per side gives no uncertainty estimate; a weaker plan was observed winning on citation points | Active |

---

## Week 5: Multi-agent coordination (and the fine-tuning decision)

| ID | Decision | Why | Status |
|---|---|---|---|
| D5.1 | **Single agent with role-structured tools is the default; the four-agent workflow is opt-in** (`--multi`) | Benchmark, 18 cases: single 18/18 @ 9.8 s / $0.0025 vs. multi-agent best 17/18 @ 49.4 s / $0.0137. Handoffs added failure surface (v1 12/18, v2 13/18, v3 17/18) | Active |
| D5.2 | **Deterministic controller + four LLM roles** (planner/writer, policy researcher, claims analyst, reviewer) with **isolated tool sets** | Checkpoint 5.1 design; tool isolation enforced in code | Opt-in |
| D5.3 | **Shared evidence store** (thread-safe `WorkingMemory`) with global evidence ids across agents; the final answer is verified against it | One source of truth; grounding check still applies | Active (opt-in path) |
| D5.4 | **Reviewer checks only final drafts**; plan scoring stays with the ToT critic; ≤ 1 revision, then disclose | Grader feedback (dual role); Lab 5.1 retry cap | Active (opt-in path) |
| D5.5 | **Controller failover to the single agent** on any stage failure; degraded defaults for unparseable scope or review; 300 s workflow limit | Grader feedback (controller failure) | Active (opt-in path) |
| D5.6 | **Lossless policy handoff:** the writer receives retrieved passage text, not just evidence summaries; ids copied exactly; no other-entity facts in abstentions | v1 live failures | Active (opt-in path) |
| D5.7 | **State normalization at the scope handoff** (names and codes) | v2 live bug: "Northland" → "NO" → treated as unknown → analyst skipped | Active (opt-in path) |
| D5.8 | **Fine-tuning rejected; adapt with tools + RAG + prompts** | Facts change and must be versioned and cited; DeepSeek already 18/18; small data; 8 GB VRAM. Possible later only for the local-model backup | Rejected (documented) |
| D5.9 | **Grader fixes:** accept the authoritative date even when sensitivity runs follow; ignore doc ids, CFR section lists, and bare "42 CFR" in numeric checks; more contrast synonyms | Grader blind spots #5–#7 found by reading traces | Active |

**Known open issue (opt-in path):** data-evidence handoffs are still summarized (v3 `coverage_sp` failure). The fix would be to pass the structured payloads (e.g. `quarters_by_status`) to the writer, as D5.6 does for policy text.

---

## Week 6: Safety guardrails, evaluation metrics, human oversight, demo console

| ID | Decision | Why | Status |
|---|---|---|---|
| D6.1 | **Deterministic PII screen** (SSN, member ID, DOB, phone, email, address) refuses before any model call; logs and the review queue are PII-redacted | Checkpoint 6.1: accept only public, synthetic, or anonymized data | Active |
| D6.2 | **Retrieved-text injection screen**: the whole sentence carrying an embedded instruction is removed and the passage flagged; documents never authorize actions | Checkpoint 6.1; planted `SP-FAQ-2024-01` trap; removing only the trigger phrase left the payload readable | Active |
| D6.3 | **Calibration check in the verifier**: causal overclaims and overconfident wording trigger a rewrite; hedged negations allowed | Lab 6.1 during/post-generation calibration | Active |
| D6.4 | **Oversight policy**: 4 mandatory triggers (decision-sensitive, source conflict the answer depends on, unresolved date, causal question) withhold the conclusion; 4 recommended triggers release + queue | Checkpoint 6.1 sign-off criteria | Active |
| D6.5 | **Review queue** (SQLite): approve / revise / reject; SLA 24 h (decision-sensitive), 72 h (conflict, date, causal), 7 days (recommended); **fail closed** on expiry | Grader feedback: how long can a withheld conclusion wait? | Active |
| D6.6 | **Reviewer metrics per trigger** (approval / revision / rejection / expiry rates, median hours) | Grader feedback: do the escalation triggers work? | Active |
| D6.7 | **Release gates**: citation support ≥ 95%, numerical accuracy ≥ 95%, 0 missed mandatory escalations, 0 prohibited-action successes; why 95% is documented | Grader feedback: why 95%? | Active |
| D6.8 | **Trajectory monitor** (heuristic suspicion score, threshold 40) + structured run log | Lab 6.2 (SHADE-Arena style); log the whole trajectory | Active |
| D6.9 | **Single pipeline** (`pipeline.answer_question`) for CLI, eval, and UI, so every path gets identical safety layers | Avoid unprotected side doors | Active |
| D6.10 | **Conflict trigger precision**: mandatory only when the answer depends on the disputed fact; otherwise `background_conflict` | Live eval: 1 unnecessary escalation → 0 | Active |
| D6.11 | **Demo and review console** (Streamlit): side-by-side mode comparison with the benchmark trade-off, trace/evidence/passages/ToT views, review queue, tools explorer, eval dashboard, monitoring log | Demo request; HITL needs a reviewer interface | Active |
| D6.12 | Refusals and out-of-scope replies always go through `submit_answer`; no other-state facts when declining (main prompt) | Live eval v2 slips | Active |

---

## Week 7: Real Illinois data and local-model tuning

| ID | Decision | Why | Status |
|---|---|---|---|
| D7.1 | **Add one real state (Illinois) beside the synthetic states**, not instead of them: CMS SDUD 2022Q1-2026Q1 (FFS and MCO), monthly Medicaid enrollment, HFS Preferred Drug List versions 2022-2026 | Real data shows the design works outside the test environment; the synthetic states stay the regression suite because only they have known answers | Active |
| D7.2 | **Snapshots with provenance** (`data/real/illinois/`, `manifest.json`: dataset id and version, URL, retrieval time, SHA-256); builds are offline; fetches retry with backoff and resume from a cache | Reproducibility; the first live fetch died on a transient DNS failure | Active |
| D7.3 | **Drug classes by name pattern with a reviewed list** (SDUD names are truncated to 10 characters); fixed-ratio combination products (Soliqua, Xultophy, SGLT2 + metformin) excluded | Each class means one mechanism; documented limitation vs. NDC-to-ingredient mapping | Active |
| D7.4 | **Real-data caveats are data, not prose**: `utilization_type` (FFS/MCO), `data_source`, pre-rebate amounts, suppressed rows NULL (lower bounds), latest quarter `provisional` (< 6 months before the release) | The agent's existing coverage and suppression logic applies unchanged | Active |
| D7.5 | **Real policy dates need a human**: dates proposed from documents enter memory as candidates; only a recorded approval (`approvals.json`, `medicaidiq approve-fact`) makes one usable; never inferred from claims | Feasibility check (D-real): a wrong date silently corrupts every pre/post; the real PDL turned out to be ambiguous (10/01 archive label vs. "Effective October 20, 2026" PDF header, and the archived 10/01 PDF link serves the 10/20 file) | Active |
| D7.6 | Unverified claims removed from the generated documents (that the PDL binds managed-care plans; that non-preferred means PA required): the HFS pages don't state them | Never claim what the source doesn't say | Active |
| D7.7 | **`local-fast` provider**: same llama.cpp server, Qwen3 thinking off per request (`chat_template_kwargs`) | Local-model plan: tool selection doesn't need long hidden reasoning | Pending benchmark |
