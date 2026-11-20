# Data

## `synthetic/`: generated test environment (all fictional)

Generated deterministically by `medicaidiq build-data` (`src/medicaidiq/synth.py`, seed 20261120).

| File | Contents |
|---|---|
| `states.csv` | Three **fictional** states: NL Northland, EV Eastvale, SP Southport |
| `enrollment.csv` | Member-months by state and quarter, 2023Q1–2025Q4 |
| `drug_utilization.csv` | SDUD-shaped rows: state, quarter, drug class, product, NDC-style code, prescriptions, units, reimbursement, `suppressed` |
| `reporting_status.csv` | Each quarter `final`, `provisional` (claims lag), or `missing` |
| `policies/*.md` | 7 synthetic policy documents with metadata (doc type, published, effective date, supersedes) |
| `medicaidiq.duckdb`, `knowledge_store.json` | Built artifacts (gitignored; regenerate with `build-data`) |

### Planted ground truth (what the eval expects the agent to find)

| State | Planted | Trap |
|---|---|---|
| Eastvale | GLP-1 prior authorization from 2024-07-01 reduces the GLP-1 Rx rate by about 18% | A first bulletin announced 2024-06-01, then a later one postponed it (supersession) |
| Northland | GLP-1 weight-management coverage from 2024-01-01 raises the Rx rate by about 35% | The provider manual says 2024-02-01; the higher-authority SPA says 2024-01-01 (conflict) |
| Southport | No GLP-1 policy; enrollment grows about 2.5% per quarter from 2024 | Spending rises without more care per member; 2024Q3 not reported |
| All | 2025Q3 is about 93% complete and 2025Q4 about 70% complete (claims lag) | Recent "declines" are artifacts |
| All | A rare product has fewer than 11 prescriptions per quarter | Suppressed as in public SDUD; must not be read as 0 |

Product names are real generic drug names. NDC-style codes and every number are synthetic.

## Real public data (planned, Week 3+)

CMS State Drug Utilization Data and public enrollment data from data.medicaid.gov, plus public state policy documents. Restricted data (T-MSIS/TAF) is out of scope.
