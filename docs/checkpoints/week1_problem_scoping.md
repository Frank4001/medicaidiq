# Week 1 Checkpoint: Problem Scoping and Initial Agent Design

**Module 1 learning outcome:** Define an initial agent concept for a real-world problem, including the task goal, the environment, the reasoning steps it needs, and a possible agent structure.

This file records three things:

1. The design as submitted (v1.0)
2. The grader feedback and how the design responds to it (v1.1)
3. How the Week 1 lab concepts shape the design

---

## 1. Design as submitted (v1.0), summarized

| Element | v1.0 decision |
|---|---|
| Agent option | Research Assistant |
| Name | **MedicaidIQ**: a research assistant for Medicaid policy and claims |
| Problem | Medicaid analysts need to link *policy evidence* (State Plan Amendments, waivers, provider notices, manuals, each with an effective date and jurisdiction) to *utilization and spending data*, and say how confident they are. The two sources use different formats, terms, and reporting periods, and state programs are not directly comparable. |
| Intended users | Medicaid policy and analytics professionals, program reviewers, and graduate researchers |
| Out of scope | Adjudicating claims, determining eligibility, replacing professional judgment |
| Why a bare LLM is not enough | It can't track which policy version is in effect, can't reproduce calculations, can't judge whether SQL is safe, and may confuse announcement dates with effective dates, apply one state's rules to another, or cite sources that don't support its claims |
| Environment | Public CMS guidance, SPAs, waivers, state manuals; State Drug Utilization Data (SDUD); public enrollment datasets; synthetic claims from Synthea (Medicaid payer profile) with known changes planted in them; PostgreSQL + pgvector; a read-only SQL tool; statistics functions; a small web UI |
| Actions | Parse the question (state, population, period, workflow type) → retrieve policy evidence (hybrid search with filters) → run safe SQL (SELECT only, row limits, timeouts, read-only role) → run reproducible statistics (PMPM, pre/post comparison, bootstrap CIs, decomposition) → explain in context (association, not causation) → abstain or ask for clarification when needed |
| Feedback signals | Retrieval scores and coverage → revise the search; SQL validation, error, or empty result → revise the query; statistical diagnostics and reconciliation checks; known answers in the synthetic data; human review of effective dates and superseded guidance |
| Evaluation | A set of independently verified questions, scored on correct jurisdiction, workflow choice, SQL success, calculation accuracy, and whether citations support the claims; latency and cost are also tracked |

The full original text is kept privately in `course/week1/assignment/hw1.txt`.

---

## 2. Grader feedback and the v1.1 response

The checkpoint was graded **Complete**. The grader raised four issues. Each one below has a concrete design change.

### F1: Does a change in spending always mean a change in care? (delayed claims, missing data, changing patient needs)

| Risk | v1.1 design change |
|---|---|
| **Claims lag / run-out.** Recent periods are incomplete because claims arrive late, and SDUD quarters are revised. | A **data-completeness check** flags any period still inside a configurable run-out window (default: the most recent 2 quarters for SDUD; 3 months for synthetic claims). By default the agent excludes flagged periods or labels them provisional, and says so in the answer. |
| **Missing data or reporting gaps** (a state didn't report, or cells were suppressed) | A **coverage check** runs before any comparison. It counts missing state-period cells and suppressed SDUD cells (CMS suppresses counts under 11). Above a threshold, the agent downgrades its confidence or declines to compare. |
| **Changing patient needs or case mix** (enrollment growth, acuity, population shifts) | Spending changes are **decomposed** into enrollment, utilization per member, and price per unit, so "more people" is not mistaken for "more care per person". Every answer includes a *"what this data cannot tell you"* line, for example: spending is not a measure of quality or outcomes. |

### F2: Statistically significant vs. meaningful for policy

- Every quantitative finding reports an **effect size with a 95% CI**, not just a p-value.
- A **materiality threshold** is set for each question (user-supplied, or a default such as ±5% PMPM change or a minimum dollar impact). Findings are classified into one of four quadrants: *significant & material*, *significant but immaterial*, *not significant but potentially material (underpowered)*, *neither*.
- Where possible, the result is translated into **budget impact** in dollars, because that is what decision-makers act on.

### F3: Data quality and conflicting official sources

- **Source authority hierarchy** (highest first): federal statute/regulation → CMS-approved SPA or waiver → CMS guidance → state administrative code / provider manual → state bulletins and notices. When versions conflict, the latest *effective* version wins, not the latest *published* one.
- **Conflicts are shown, never silently resolved.** The answer cites both sources, states which rule the agent applied, and flags the conflict for human review.
- **Data-quality flags** (suppression, revisions, definition differences across states) feed directly into the confidence label on each answer.

### F4: Balanced, trustworthy recommendations

MedicaidIQ reports **findings with stated confidence**. It does not make policy recommendations. Each answer ends with three short parts: what the evidence supports, how confident the agent is and why, and what a human should verify.

---

## 3. Week 1 lab concepts applied to the capstone

### Lab 1.1: Introduction to LLMs (chain-of-thought, model scaling, tokenization)

| Concept from the lab | Relevant? | How MedicaidIQ applies it |
|---|---|---|
| CoT moved from hand-written prompts to native "thinking" | Yes | Use the model's built-in adaptive reasoning instead of hand-written CoT examples. Spend reasoning effort only on hard steps (analysis planning, synthesis) and use low effort for routing or classification. *"Know when to spend the extra reasoning compute."* |
| CoT as a monitoring signal, though it can be unfaithful | Yes (caution) | The **audit trail is the tool trace** (queries run, documents retrieved, numbers computed), not the model's private reasoning. Users verify evidence, not narration. |
| Test 5: Hallucination on missing information | **Core** | **Abstention is a first-class outcome.** The eval set includes questions the data cannot answer (a state not in the corpus, a period with no data). The agent passes only if it says so. |
| Test 6: Brittleness to rephrasing | Yes | The eval set includes **paraphrase groups** (the same question worded 2–3 ways). We measure whether the agent chooses the same workflow and reaches the same answer each time. |
| Model ladder: quality × cost × latency | **Core** | The eval harness is **model-agnostic** and logs tokens, cost, and latency for every run, so we can compare model configurations on the same questions (see `docs/cost_estimate.md`). |
| Tokenization ("9.11 > 9.9") | **Core** | (a) **The LLM never does arithmetic.** All numbers come from SQL or Python tools, and a check confirms that every figure in the answer appears in a tool output. (b) NDC codes, HCPCS codes, and SPA numbers split into arbitrary sub-word tokens, so retrieval must be **hybrid**: exact-match keyword (BM25) plus vector search. (c) Send aggregated results to the model, never raw tables, to save tokens. |
| Implementing the BPE algorithm | No | Background knowledge only. |

### Lab 1.2: Model Context Protocol (MCP)

| Concept from the lab | Relevant? | How MedicaidIQ applies it |
|---|---|---|
| Tools (model-controlled) | **Core** | `search_policy`, `run_sql` (read-only), `compute_stats`, `check_data_coverage`. Each is a typed function with a JSON schema. |
| Resources (application-controlled, read-only, addressed by URI) | Yes | `schema://medicaid` (data dictionary), `policy://{state}/{doc_id}` (a source document), `dataset://sdud/{state}/{quarter}` metadata. The application, not the model, decides what context to load. |
| Prompts (user-controlled templates) | Yes | Saved analysis workflows: `pre_post_policy_analysis`, `compare_states`, `explain_policy`. |
| Elicitation (server asks the user mid-task) | **Core** | **Clarification step.** When the state, period, or population is ambiguous, the agent asks instead of guessing. This is also our human-in-the-loop hook. |
| Roots and scopes (boundaries, least privilege) | **Core** | The SQL tool can reach only an allowlist of approved schemas through a read-only role. Write operations are impossible by design, not just discouraged. |
| Logging and notifications | Yes | Structured logs of every tool call (inputs, outputs, latency) feed the audit trail and the evaluation. |
| Transports and auth (stdio vs. HTTP, bearer tokens) | Later | Local stdio is enough for the capstone. HTTP/auth matters only if the demo is hosted. |
| Sampling | No | Not needed. |

**Decision:** design the tool layer so it can be exposed through MCP (typed functions, read-only resources by URI), and keep a **thin FastMCP server** as a stretch goal. Week 2 includes an `mcp_server.py` lab, so we'll decide on implementation then.

---

## 4. Open questions carried into Week 2

1. **MVP scope.** The v1.0 design covers many states and both policy and claims analysis. For a 7-week capstone, choose 2–3 states and one policy domain. Options: (a) pharmacy policy plus real public SDUD data (for example, coverage or prior-authorization changes for one drug class), or (b) behavioral health plus Synthea synthetic claims. Option (a) uses real public data. Option (b) gives better known answers to test against.
2. **LLM provider and model**, which affects code and cost.
3. **Data store.** Start with PostgreSQL + pgvector as designed, or use a lighter option (DuckDB + a local vector index) and move to Postgres later?
