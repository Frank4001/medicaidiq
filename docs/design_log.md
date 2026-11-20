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
