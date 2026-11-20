# Week 2 Checkpoint: Reasoning Loop, Memory, and Tools

**Module 2 learning outcome:** Refine the agent concept by adding reasoning loops, memory, and tool use, and justify each one by the limitation it overcomes.

Week 2 is where MedicaidIQ went from design to a **working, tested agent**. This document maps the design (as submitted) to what is now implemented, shows how the grader's feedback was addressed in code, and records which lab concepts were applied.

---

## 1. Design as submitted (v2.0), summarized

| Element | Submitted design |
|---|---|
| Reasoning loop | ReAct: assess → act → observe → decide. Clarify missing essentials first; verify the effective date before analysis; check filters and coverage before reading an empty result as zero; stop when verified, when clarification is needed, or at a tool-attempt limit |
| Short-term memory | Current request: state, population, period, policy dates, retrieved passages, query results, unresolved questions, failed attempts |
| Long-term memory | Verified, reusable knowledge: document metadata, sources, data definitions, schemas, calculation methods, each with jurisdiction, effective and retrieval dates. Prior answers are not authoritative; interpretations are kept separate from verified facts |
| Tools | Policy search (with source references), read-only SQL (schema-checked, SELECT-only, limits and timeouts), calculation (PMPM, rates, period change, consistent denominators) |
| vs. prompt-only | A prompt-only answer can sound convincing while using an outdated policy or wrong effective date, or treating spending as utilization; "reason carefully" cannot supply missing evidence or verify math |

The full text is kept privately in `course/week2/assignment/hw2.txt`.

## 2. Grader feedback and the v2.1 response (implemented)

The checkpoint was graded **Complete** ("Job well done"). The grader asked three questions about the long-term knowledge base. All three are now answered **in code**, in `src/medicaidiq/memory.py`, and tested in `tests/test_memory.py`:

| Question | Mechanism | Test |
|---|---|---|
| How does newer guidance **supersede** stored information? | A document with `supersedes: <doc_id>` marks the older document's facts `superseded`. They are kept for audit but never used as evidence. | `test_explicit_supersession` |
| How are **conflicting versions reconciled**? | Source-authority hierarchy (regulation > SPA/waiver > federal guidance > manual > bulletin). The higher-authority source wins, and lower-authority disagreements are returned as `conflicts` that the answer must disclose. Equal authority with different values gives status `conflicted`: the agent does not choose, and the conflict goes to human review. | `test_higher_authority_wins_and_conflict_is_disclosed`, `test_equal_authority_disagreement_is_not_resolved` |
| What **triggers an update** to existing knowledge? | (1) Ingesting a new document re-resolves every key it touches. (2) Facts older than `review_days` are reported as `needs_review`. (3) Agent interpretations enter as `provisional` (authority 0) and become evidence only after a human `approve()`. | `test_stale_fact_triggers_review`, `test_agent_interpretation_requires_human_approval` |

The synthetic data contains **live traps** for both cases:

- **Eastvale:** a bulletin announced a June 1 start, and a later bulletin postponed it to July 1 (supersession).
- **Northland:** the provider manual says February 1, but the approved SPA says January 1 (authority conflict).

The eval checks that the agent uses the correct date and discloses the conflict.

## 3. Week 2 lab concepts applied

### Lab 2.1: Building a ReAct agent

| Concept | Applied? | How |
|---|---|---|
| Thought → Action → Observation loop | **Yes, core** | `agent.py`. Each turn requires a one-line `Thought:` followed by one tool call; observations are appended and drive the next step. |
| Max steps and `finish` action | Yes | `max_steps=10`; `submit_answer` is the finish action, with a JSON schema instead of free text. |
| Regex action parsing | **Replaced** | The lab's regex parser breaks on formatting drift. Native function calling gives schema-validated arguments. Malformed calls still come back as recoverable observations. |
| Context accumulation | Yes, plus improvement | The full history is kept, and a compact **working-memory scratchpad** (slots, evidence IDs, failed attempts) is re-injected each step so key facts aren't buried by long tool outputs. |
| Error recovery | Yes | Tool errors become observations ("SQL error: …, retry"). Repeating the same failed call stops the loop with an honest "cannot answer". |
| CoT monitoring (lab summary) | Yes, adapted | We don't rely on the model's private reasoning (D1.8). The **verifier** monitors the *output*: every number must be grounded in a tool result, and every citation must exist. |

### Lab 2.2: Tool calling in agents

| Concept | Applied? | How |
|---|---|---|
| Baseline LLM fails at arithmetic, comparison, dates | Yes | Motivates `calculate`, `pre_post_analysis`, and the grounding verifier. Our own test file shows it: the expected value hand-written into a test was wrong (3,206,132 vs. 3,205,932) and the calculator caught it. |
| ReAct vs. framework vs. API tool calling | Yes | We use **native API tool calling without LangChain**: fewer dependencies, a fully inspectable loop (an advantage for grading and debugging), and portability across providers. |
| Local vs. API models (reliability vs. cost) | **Yes, decision** | Formal comparison in [`docs/llm_selection.md`](../llm_selection.md): local Qwen3-8B on the RX 6700S vs. DeepSeek, Qwen, and Kimi hosted. |
| MCP server exposing tools | **Yes, implemented** | `src/medicaidiq/mcp_server.py` exposes the same 7 analysis tools, 2 resources (`schema://`, `policy://{doc_id}`), and 1 prompt. Tested over an in-memory MCP client. |
| Input validation for tools | Yes | JSON-schema enums for state, drug class, and topic; the SQL validator; a safe AST calculator (no `eval`). |

## 4. What each capability fixes (rubric: justification vs. prompt-only)

| Prompt-only failure mode | Capability that fixes it | Evidence |
|---|---|---|
| Uses the **announced** date (June 1) instead of the effective date (July 1) | Long-term memory with supersession + `get_policy_timeline` | `test_built_store_handles_planted_traps`; eval `prepost_ev` checks the date used |
| Silently follows a lower-authority source | Authority hierarchy + conflict disclosure | eval `conflict_nl` |
| Invents or miscalculates a percentage | Analysis tools + grounding verifier with a retry | `test_ungrounded_number_is_caught_and_corrected` |
| Reads "spending rose" as "more care" | `spending_decomposition` | `test_spending_growth_driven_by_enrollment_not_utilization`; eval `decomp_sp` |
| Treats missing or lagged quarters as real declines | `check_data_coverage` + exclusion in `pre_post_analysis` | `test_missing_quarter_is_excluded_and_disclosed`, `test_provisional_quarters_are_excluded` |
| Writes destructive or invalid SQL | 4-layer SQL guard | `tests/test_guardrails.py` (17 cases) |
| Guesses when the question is ambiguous | `ask_user` (clarification) | `test_clarification_*`; eval `clarify` |
| Loops forever or gives up silently | Step limit + repeated-failure stop + honest "cannot answer" | `test_max_steps_*`, `test_repeated_identical_failure_stops_loop` |

## 5. Open items for Week 3+

- **Live LLM validation:** needs a DeepSeek key, and the local server needs to be started by the user (see `docs/llm_selection.md` §6).
- **Retrieval:** BM25 only for now; add vector search to make it hybrid (D1.3), plus chunking with section references.
- **Real data:** add a public CMS SDUD extract and real public policy documents alongside the synthetic test environment.
- Expand the eval from 9 to about 40 cases, with more paraphrase groups and adversarial prompts.
