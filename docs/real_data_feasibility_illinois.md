# Feasibility Check: Moving to Real Illinois Data

*Run 2026-11-20. Read-only probe of public sources; no Illinois data or documents have been added to the agent yet. Raw probe output was kept outside the repo.*

## Verdict

**Feasible for one state and one drug class (Illinois, GLP-1) as a Week 5–6 stretch goal.** The utilization and enrollment data are public, machine-accessible, and fit our schema almost directly. Policy documents are reachable and allowed by the site's robots.txt, and the drug list comes as a spreadsheet. The open question is where Illinois publishes GLP-1-specific prior-authorization criteria.

## 1. Utilization data: CMS State Drug Utilization Data (SDUD) ✅

- **Access:** the data.medicaid.gov datastore API supports server-side filters (`state = IL`, `product_name LIKE`). Datasets exist for 2020–2026, last updated July 2026.
- **Volume:** 132,996 Illinois rows in 2024 across all drugs; **968 GLP-1 rows for 2022 Q1 to 2026 Q1**. That's small.
- **Schema:** matches ours, plus `utilization_type` (FFS vs. MCO), `suppression_used`, and the NDC split into labeler, product, and package codes.

Illinois GLP-1 prescriptions (non-suppressed rows only; reimbursement is **pre-rebate**):

| Quarter | FFS Rx | MCO Rx | Total Rx | Reimbursed |
|---|---:|---:|---:|---:|
| 2022 Q1 | 1,112 | 33,919 | 35,031 | $45.3M |
| 2023 Q1 | 2,315 | 58,757 | 61,072 | $71.2M |
| 2024 Q1 | 1,980 | 60,521 | 62,501 | $70.3M |
| 2024 Q2 | 4,364 | 61,843 | 66,207 | $72.3M |
| 2025 Q1 | 5,217 | 64,181 | 69,398 | $75.9M |
| 2025 Q4 | 2,398 | 71,483 | 73,881 | $77.2M |
| 2026 Q1 | 2,774 | 49,319 | **52,093** | $55.3M |

**What the real data shows, all of which the agent's design already handles:**

| Observation | Matching design feature |
|---|---|
| **~95% of GLP-1 prescriptions are managed care (MCO)** | New: analyses must split or label FFS vs. MCO; FFS-only policy documents may not apply to MCO members |
| **2026 Q1 drops about 30%**, almost certainly incomplete reporting (data updated July 2026) | Provisional-quarter exclusion (D1.11), the same trap as our synthetic 2025 Q4 |
| **23% of GLP-1 rows suppressed** (226 of 968; small products and packages) | Suppression handling: never treated as 0; totals are lower bounds |
| **Product names are truncated to 10 characters** ("OZEMPIC 0.", "MOUNJARO 1"); the brand-name search may miss combination products (e.g., insulin/GLP-1) or generics | New: map by NDC using the FDA NDC Directory (active ingredient), not by name |
| **The FFS count doubled in 2024 Q2** (1,980 → 4,364) | A real "did something change?" question: needs policy documents to explain, and the agent must not invent a cause |
| **Obesity-labelled products are rare** (2024: Wegovy 178 Rx in total; Zepbound appears only in suppressed rows) | Consistent with limited coverage for weight management, but **not verified**: the policy must come from HFS documents, not be inferred from claims |

## 2. Enrollment denominators ✅

- **Source:** data.medicaid.gov "State Medicaid and CHIP Applications, Eligibility Determinations, and Enrollment Data" (October 2026 release), monthly by state, through June 2026.
- **Preliminary vs. updated** versions are both published: use `U` (updated) rows, which is another real revision issue to disclose.
- **Illinois total Medicaid enrollment fell about 22%**, from 3.52M (July 2023) to 2.74M (October 2025), as pandemic-era continuous coverage ended. Over the same period GLP-1 Rx *rose*, so per-member rates rose faster than raw counts. This is a real instance of the denominator and case-mix issue from the Week 1 feedback (F1): a pre/post comparison without per-member rates would be misleading.
- **Gap:** adult-only enrollment is reported only from July 2024 on, so adult-specific rates can't span the full period.
- Approximation: member-months ≈ the sum of monthly enrollment in each quarter.

## 3. Illinois policy documents (HFS) ✅ access / ⚠️ content

- **Access:** hfs.illinois.gov returns HTTP 200 to our clearly identified client. **robots.txt permits it**: specific ad and SEO bots are blocked, and the default for all other agents is `Allow: /` (only `/cgi-bin/` is excluded). Fetch slowly (≥ 2 s between requests).
- **Available:**
  - **Preferred Drug List dated 10/20/2026 as PDF *and* XLSX**: the spreadsheet can be parsed without PDF tooling.
  - **Archived PDLs**: a real version history, which feeds the supersession logic (D2.8).
  - A Criteria and Forms page (15 PDFs), Drug Prior Approval Information, pharmacy provider notices, and a *Uniform Prior Authorization* workgroup (suggesting criteria coordinated across MCOs; to be confirmed).
- **Not found yet:** GLP-1-specific prior-authorization criteria. None of the 15 criteria PDFs match GLP-1 or diabetes terms. Likely places: PDL footnotes, or the separate lookup at ilpriorauth.com (not checked).

## 4. Plan if we proceed

| Step | Work | Effort |
|---|---|---|
| 1 | `ingest_sdud.py`: API pull for IL, GLP-1 by NDC (FDA NDC Directory ingredient list), keep `utilization_type` | ~0.5 day |
| 2 | Enrollment loader (updated rows → quarterly member-months) | ~0.5 day |
| 3 | Schema: add `utilization_type`; tools gain an optional FFS/MCO filter; reporting status derived from data recency | ~0.5 day |
| 4 | Policy: parse the PDL XLSX + archived PDLs (version history); add PDF text extraction for notices | ~1 day |
| 5 | Knowledge store: human-verified Illinois effective dates (no automatic inference) | ~0.5 day + review |
| 6 | Eval: human-verified IL cases (totals reconcile with CMS, documented dates, direction around a known change); synthetic states stay as the regression suite | ~1 day |

**About 4–5 days in total.** The synthetic environment stays: it is the only place with known answers.

## Risks

- Pre-rebate reimbursement overstates net cost, and rebates aren't public. The agent must say so.
- MCO plans may apply criteria that differ from FFS documents.
- Without verified policy dates, a pre/post analysis for Illinois can't be run responsibly. Policy dates must come from HFS documents and human review.
- SDUD revisions: the July 2026 update may change in later releases, so snapshots need version dates (already our practice).
