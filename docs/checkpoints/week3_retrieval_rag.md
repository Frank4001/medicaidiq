# Week 3 Checkpoint: RAG and Retrieval Design Integration

**Module 3 learning outcome:** Refine the agent design by adding RAG and data-grounding decisions.

Last week's checkpoint was a standalone retrieval prototype (CMS telehealth pages, bge-small embeddings, SQLite, top 3 plus the adjacent paragraph). This week that design is **integrated into the full MedicaidIQ agent**, pointed at a real external source, and evaluated live. This document is organized by the five rubric criteria, plus the grader's follow-up questions.

---

## 1. Architectural decision: is retrieval required? **Yes, for policy text. No, for numbers.**

MedicaidIQ answers two kinds of questions, and they need different grounding:

| Question type | Example | Grounding | Why |
|---|---|---|---|
| **What a policy says** (criteria, conditions, requirements, effective-date rules) | "What clinical criteria does Eastvale require for GLP-1 PA?" | **Retrieval (RAG)** | The answer is free text that varies by state and changes over time; the model's training data can't know a state's current manual |
| **What the data shows** (rates, changes, spending) | "Did GLP-1 prescribing change after PA?" | **SQL + statistics tools** (no RAG) | Numbers must be computed, not retrieved or generated (D1.2) |
| **Which date or version is authoritative** | "When did the policy take effect?" | **Long-term memory** (knowledge store) | A resolved fact with supersession and authority rules (D2.8). Retrieval supplies the supporting text |

So retrieval is one tool in the agent's ReAct loop, not a fixed pipeline step. The model calls `search_policy` when a question depends on document text, and analysis questions skip it. This is the main Week 3 design decision (D3.1).

## 2. Semantic retrieval with an external data source (implemented)

**External source (real, public):** 10 federal Medicaid regulations from the official **eCFR API** (Title 42: §§ 430.12, 430.20, 440.120, 447.205, 447.256, 447.512, 447.518, 456.703, 456.705, 456.709), plus the **data.medicaid.gov** State Drug Utilization Data dataset description. They are fetched by `medicaidiq fetch-public` (`src/medicaidiq/ingest_public.py`). Each document is saved with its source URL, eCFR version date (2026-11-18), retrieval timestamp, and SHA-256 hash, carrying over the provenance design from the Checkpoint 3.1 prototype.

*Why not Medicaid.gov pages, as in the prototype?* They return HTTP 403 to automated clients, and we don't try to get around that. eCFR and data.medicaid.gov are official sources built for programmatic access, and regulations are the highest-authority level in our source hierarchy (D1.13).

**Combined corpus:** 57 paragraph chunks from 18 documents: the 11 public documents above + 7 synthetic documents for the fictional states (Northland, Eastvale, Southport), which carry the state-specific traps.

**Pipeline** (`src/medicaidiq/retrieval.py`):

```
query ─► 1 filter (jurisdiction + federal, as-of date)
      ─► 2 BM25 keyword rank  ┐
      ─► 3 dense rank (bge-small-en-v1.5, cosine) ┘─► 4 Reciprocal Rank Fusion → 15 candidates
      ─► 5 MMR rerank (λ = 0.7) → top 4
      ─► 6 + next paragraph of each hit (adjacency) → ≤ 6 passages
      ─► 7 relevance / scope gate → status: ok | low_relevance | out_of_scope
```

Embeddings run locally (fastembed / ONNX, about 7 s to load, $0). The index is a cached NumPy matrix keyed by a hash of the corpus, so it rebuilds automatically when documents change. At 57 chunks a vector database would add setup with no benefit; the retriever interface allows swapping one in later.

## 3. Evidence that retrieval changes the output

**Ablation, live on DeepSeek, 5 retrieval cases** (`eval/run_eval.py --category rag [--no-retrieval]`):

| Case | Without retrieval | With retrieval |
|---|---|---|
| `rag_ev_criteria`: Eastvale's PA criteria | ❌ `cannot_answer` | ✅ exact criteria, cited |
| `rag_compare_states`: Northland vs. Eastvale | ❌ `cannot_answer` | ✅ side-by-side, every rule attributed |
| `rag_federal_spa`: SPA effective-date rule | ❌ `cannot_answer` | ✅ correct reading of 42 CFR 430.20 |
| `rag_federal_dur`: pharmacy screening rule | ❌ `cannot_answer` | ✅ cites 42 CFR 456.705(b) |
| `rag_out_of_scope_state`: Illinois | ✅ `cannot_answer` | ✅ `cannot_answer` (refuses correctly) |
| **Pass rate** | **1/5** | **5/5** |

**The same question, both ways** (`rag_ev_criteria`, verbatim agent output):

> **Without retrieval:** "I can confirm which Eastvale document governs GLP-1 prior authorization (EV-MAN-2024-PH, effective 2024-07-01, superseding bulletin EV-BUL-2024-007), but I cannot state the clinical approval criteria … I have no retrieved text to quote for criteria such as BMI thresholds, diagnosis requirements, step therapy…"
>
> **With retrieval:** "Eastvale's Pharmacy Manual (Chapter 5, effective July 1, 2024) lists two alternative clinical pathways for GLP-1 prior authorization: type 2 diabetes with A1c >= 7.0 after a metformin trial, or a BMI >= 35 with a documented weight-related comorbidity. Approvals last 12 months."
> Findings: "pathway (1) … A1c >= 7.0 after a metformin trial **[EV-MAN-2024-PH#p1]**", "pathway (2): BMI >= 35 … **[EV-MAN-2024-PH#p1]**"

Without retrieval the agent declines instead of guessing, which is the hallucination guard (D1.4) working. With retrieval the answer is specific, cited at paragraph level, and passes the grounding verifier: every number ("7.0", "35") appears in the retrieved text. Full traces are in `eval/results/`.

**Retrieval also exposed a corpus gap.** For the SPA question, the agent cited 42 CFR 430.20(b)(2), which defers to § 447.256, then noted that § 447.256 was "not in this corpus" and declined to state its rule. We added § 447.256 to the corpus, and the rebuilt index picked it up automatically.

## 4. Key retrieval design choices

| Choice | Decision | Why |
|---|---|---|
| Data sources | eCFR (federal regulations) + data.medicaid.gov + synthetic state documents | Official, citable, machine-accessible; the synthetic states carry known traps |
| Segmentation | **Paragraph-level.** Regulations are grouped by top-level paragraph (a), (b)…, with sub-items (1), (i) kept under their parent; split above about 180 words | Splitting a rule from its conditions is the main risk. A regulation's paragraph structure is its natural unit of meaning, and gives exact citations such as `42-CFR-430.20(b)` |
| Chunk text for embedding | Document title + paragraph label + text | DPR-style "title [SEP] passage" (Lab 3.1): the title disambiguates short paragraphs |
| Embedding model | `BAAI/bge-small-en-v1.5` (384-dim), with the query instruction prefix | Same model as the prototype; strong small retriever; runs locally for free |
| Hybrid ranking | BM25 + dense, fused by RRF (k = 60) | Dense captures meaning; BM25 catches exact tokens (CFR numbers, drug names, document IDs) that embeddings blur (D1.3) |
| **Number retrieved** | **Top 4 after MMR, plus up to 2 adjacent paragraphs = at most 6** | Enough to cover a rule and its exceptions; fits the context budget |
| Diversity | MMR, λ = 0.7 (Lab 3.2) | Specific questions want relevance first, but near-duplicates (an original bulletin and its replacement) shouldn't crowd out other evidence |
| Scope labels | Every passage carries `jurisdiction`, `scope` (federal or state), `superseded_by`, `source_url`, and its scores | The agent and the user can see why a passage was used and whether it may be applied |

## 5. Failure modes and mitigations

| Failure mode | How it would show up | Mitigation | Evidence |
|---|---|---|---|
| **Wrong jurisdiction**: federal or another state's text answers a state question | "Illinois requires …" invented from federal text | Hard jurisdiction filter; federal passages labelled "federal (all states)"; a state request with no matching state document returns status **`out_of_scope`** regardless of similarity | `rag_out_of_scope_state` passes. A live run first showed an Illinois query scoring 0.681 on federal text, just above the relevance threshold, which is why the scope rule now overrides the score |
| **Missed qualification**: the rule ranks high, its exception doesn't | A yes/no answer that drops "unless …" | Adjacent-paragraph expansion (from the prototype's telehealth finding); paragraph-group chunking keeps (b)(1)–(3) together | `test_adjacent_expansion_brings_in_the_exception` |
| **Irrelevant top hit** | The answer is built from the nearest unrelated passage | Relevance gate: best dense similarity < 0.68 → `low_relevance`; the prompt forbids answering from it | Calibrated: relevant ≥ 0.73, off-topic ≤ 0.66 ("pizza" 0.45, "adult dental" 0.656) |
| **Superseded document** | Using the postponed June 1 date | `superseded_by` on every passage + the knowledge store's supersession rules | `test_superseded_document_is_labelled` |
| **Cross-state contamination** in comparisons | Applying Eastvale's PA to Northland | The prompt requires one search per state and attribution of every rule | Live `rag_compare_states` trace: `search_policy(NL)`, then `search_policy(EV)`; the answer attributes each rule |

## 6. Grader feedback on Checkpoint 3.1 and the response

| Feedback | Response |
|---|---|
| *Show a complete agent response using the retrieved information* | §3: full live agent answers, with and without retrieval; the answers cite paragraph-level chunk IDs and pass the grounding verifier. |
| *Test with more state-specific policies and conflicting information* | State-specific: `rag_ev_criteria`. Conflicting: Northland's SPA vs. its manual (`conflict_nl`) and Eastvale's superseded bulletin, surfaced through retrieval labels and the knowledge store. Missing state: `rag_out_of_scope_state`. |
| *How would the agent handle two states with different reimbursement rules for the same service?* | Demonstrated live with Northland vs. Eastvale GLP-1 coverage (`rag_compare_states`): one search per state, rules attributed per jurisdiction, differences stated explicitly. Agent output: *"The two states take opposite approaches. Northland (NL) does not require prior authorization … Eastvale (EV) requires prior authorization for all GLP-1 receptor agonists effective July 1, 2024 …"*, each rule cited to its own state's document. If two sources in the *same* state disagree, the authority hierarchy decides and the conflict is disclosed (D2.8). |

## 7. Other Week 3 changes

- **Deterministic write-intent guardrail**, carried over from Week 2: requests to delete, insert, or alter data are refused before any LLM call. It avoids false positives on analysis phrasing ("Did enrollment drop?"). Tested in both directions.
- **Bug found live and fixed:** a long final answer was cut off mid-JSON, and the agent accepted the unparseable payload without verifying it. Unparseable answers are now rejected with a "resubmit shorter" observation, and the output limit was raised to 4,096 tokens. A regression test was added.
- **Grader fixes:** CFR citations are no longer counted as numeric claims; keyword lists gained synonyms ("causation", "opposite"). Three keyword-grader false negatives so far, so keyword grading is a known limitation to revisit in the evaluation module.

## 8. Results

| | Week 2 | Week 3 |
|---|---|---|
| Eval cases | 9 | **14** (+5 retrieval) |
| DeepSeek pass rate | 9/9 | **14/14** |
| Average latency / cost per question | 9.0 s / $0.0023 | 7.8 s / $0.0024 |
| Offline tests | 49 | **76** |

## 9. Limitations and next steps

- State documents are still synthetic. Real state manuals are mostly behind bot protection or in PDFs. Adding PDF ingestion is a possible next step.
- The relevance threshold (0.68) was calibrated on a small set of queries. Expanding the eval will test it.
- Keyword graders are brittle. A structured rubric or an LLM judge for open-ended cases will be considered in the evaluation module.
- The corpus is small (57 chunks). Retrieval quality at scale (hundreds of documents, tables, cross-references) is untested.
