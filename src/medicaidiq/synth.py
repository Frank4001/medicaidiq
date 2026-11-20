"""Generate the synthetic test environment: drug-utilization data and a policy corpus.

Why synthetic? Evaluation needs *known answers*. Each planted effect below is a
ground truth the agent should recover, and each planted data problem is a trap
it should detect (feedback F1/F3 from Week 1):

  NL (Northland): GLP-1 coverage expansion, effective 2024-01-01   -> GLP-1 Rx rate x1.35
                  A provider manual wrongly says 2024-02-01         -> source conflict (SPA wins)
  EV (Eastvale):  GLP-1 prior authorization, effective 2024-07-01   -> GLP-1 Rx rate x0.82
                  First bulletin announced 2024-06-01, then postponed -> supersession trap
  SP (Southport): No GLP-1 policy change; enrollment grows ~2.5%/qtr -> spending up, utilization flat
                  2024Q3 not reported                               -> coverage gap
  All states:     2025Q3/2025Q4 incomplete (claims lag)             -> provisional periods
                  Rare product with < 11 Rx                         -> suppressed cells (as in public SDUD)

States are FICTIONAL so no synthetic number can be mistaken for a real state's data.
The schema mirrors CMS State Drug Utilization Data so real data can replace it later.
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

import duckdb

from .paths import DB_PATH, POLICY_DIR, SYNTHETIC_DIR

SEED = 20261120
QUARTERS = [(y, q) for y in (2023, 2024, 2025) for q in (1, 2, 3, 4)]
DATA_AS_OF = "2026-01-15"
STATES = {
    "NL": {"name": "Northland", "base_mm": 900_000, "growth": 0.0},
    "EV": {"name": "Eastvale", "base_mm": 1_500_000, "growth": 0.0},
    "SP": {"name": "Southport", "base_mm": 600_000, "growth": 0.025},
}
# drug_class -> (products, baseline Rx per 1,000 member-months, avg reimbursement per Rx)
PRODUCTS = {
    "GLP1": ([("semaglutide injection", "00169-4130", 0.60),
              ("dulaglutide injection", "00002-1433", 0.30),
              ("liraglutide injection", "00169-4060", 0.10)], 12.0, 910.0),
    "SGLT2": ([("empagliflozin tablet", "00597-0152", 0.65),
               ("dapagliflozin tablet", "00310-6205", 0.35)], 15.0, 560.0),
    "INSULIN": ([("insulin glargine", "00088-2220", 0.55),
                 ("insulin lispro", "00002-7510", 0.45)], 25.0, 310.0),
}
RARE_PRODUCT = ("exenatide injection", "00310-6512", "GLP1")  # tiny volume -> suppressed
LAG_COMPLETENESS = {(2025, 3): 0.93, (2025, 4): 0.70}          # claims run-out
MISSING = {("SP", 2024, 3)}                                     # reporting gap
SUPPRESSION_THRESHOLD = 11


def _effect(state: str, drug_class: str, year: int, quarter: int) -> float:
    """Planted policy effects on utilization rate."""
    if drug_class != "GLP1":
        return 1.0
    if state == "NL" and (year, quarter) >= (2024, 1):
        return 1.35
    if state == "EV" and (year, quarter) >= (2024, 3):
        return 0.82
    return 1.0


def _quarter_index(year: int, quarter: int) -> int:
    return QUARTERS.index((year, quarter))


def build_tables(rng: random.Random) -> tuple[list[dict], list[dict], list[dict]]:
    enrollment, utilization, status = [], [], []
    for st, meta in STATES.items():
        for (y, q) in QUARTERS:
            if (st, y, q) in MISSING:
                status.append({"state": st, "year": y, "quarter": q, "status": "missing",
                               "note": "State did not submit data for this quarter."})
                continue
            completeness = LAG_COMPLETENESS.get((y, q), 1.0)
            status.append({
                "state": st, "year": y, "quarter": q,
                "status": "provisional" if completeness < 1 else "final",
                "note": (f"Claims run-out incomplete; roughly {round(completeness * 100)}% of "
                         "expected claims received as of data date." if completeness < 1 else ""),
            })
            idx = _quarter_index(y, q)
            growth_qtrs = max(0, idx - _quarter_index(2024, 1) + 1) if meta["growth"] else 0
            mm = round(meta["base_mm"] * (1 + meta["growth"]) ** growth_qtrs * rng.gauss(1, 0.005))
            enrollment.append({"state": st, "year": y, "quarter": q, "member_months": mm})

            for dclass, (products, rate, price) in PRODUCTS.items():
                price_q = price * (1.0075 ** idx)  # ~3%/yr unit-price trend
                class_rx = rate * mm / 1000 * _effect(st, dclass, y, q) * rng.gauss(1, 0.02)
                for name, ndc, share in products:
                    rx = round(class_rx * share * completeness)
                    reimb = round(rx * price_q * rng.gauss(1, 0.01), 2)
                    utilization.append({"state": st, "year": y, "quarter": q, "drug_class": dclass,
                                        "product_name": name, "ndc": ndc, "prescriptions": rx,
                                        "units": rx * (4 if dclass == "GLP1" else 30),
                                        "reimbursement_usd": reimb, "suppressed": False})
            rare_rx = rng.randint(3, 9)  # always below threshold -> suppressed like public SDUD
            utilization.append({"state": st, "year": y, "quarter": q, "drug_class": RARE_PRODUCT[2],
                                "product_name": RARE_PRODUCT[0], "ndc": RARE_PRODUCT[1],
                                "prescriptions": None if rare_rx < SUPPRESSION_THRESHOLD else rare_rx,
                                "units": None, "reimbursement_usd": None, "suppressed": True})
    return enrollment, utilization, status


POLICIES = [
    dict(doc_id="NL-SPA-2023-014", state="NL", doc_type="state_plan_amendment", topic="glp1_coverage",
         drug_class="GLP1", title="State Plan Amendment 23-014: Coverage of GLP-1 agonists for chronic weight management",
         published="2023-11-20", effective_date="2024-01-01", supersedes="",
         body="""Northland Medicaid amends its outpatient prescription drug benefit to cover GLP-1 receptor
agonists for chronic weight management in adults with BMI >= 30, or BMI >= 27 with at least one
weight-related comorbidity. This amendment was approved by the federal Medicaid agency and is
effective January 1, 2024. Coverage for type 2 diabetes is unchanged."""),
    dict(doc_id="NL-MAN-2024-RX", state="NL", doc_type="provider_manual", topic="glp1_coverage",
         drug_class="GLP1", title="Northland Pharmacy Provider Manual, Section 7.4 (GLP-1 agonists)",
         published="2024-02-01", effective_date="2024-02-01", supersedes="",
         body="""Section 7.4 describes billing for GLP-1 receptor agonists prescribed for weight management.
The manual states that weight-management coverage begins February 1, 2024. Pharmacies should use
the standard prior-authorization-free pathway for patients meeting the BMI criteria."""),
    dict(doc_id="EV-BUL-2024-007", state="EV", doc_type="provider_bulletin", topic="glp1_prior_auth",
         drug_class="GLP1", title="Provider Bulletin 2024-07: Prior authorization for GLP-1 agonists",
         published="2024-05-15", effective_date="2024-06-01", supersedes="",
         body="""Eastvale Medicaid will require prior authorization (PA) for all GLP-1 receptor agonists.
PA will require a documented diagnosis of type 2 diabetes or qualifying weight-management criteria.
This requirement is scheduled to take effect June 1, 2024."""),
    dict(doc_id="EV-BUL-2024-011", state="EV", doc_type="provider_bulletin", topic="glp1_prior_auth",
         drug_class="GLP1", title="Provider Bulletin 2024-11: GLP-1 prior authorization start date postponed",
         published="2024-06-10", effective_date="2024-07-01", supersedes="EV-BUL-2024-007",
         body="""This bulletin replaces Provider Bulletin 2024-07. To allow prescribers time to submit
documentation, the GLP-1 prior authorization requirement now takes effect July 1, 2024. Claims for
GLP-1 agonists with dates of service before July 1, 2024 do not require PA."""),
    dict(doc_id="EV-MAN-2024-PH", state="EV", doc_type="provider_manual", topic="glp1_prior_auth",
         drug_class="GLP1", title="Eastvale Pharmacy Manual, Chapter 5: Prior authorization criteria",
         published="2024-07-01", effective_date="2024-07-01", supersedes="",
         body="""Chapter 5 lists prior authorization criteria for GLP-1 receptor agonists effective
July 1, 2024: (1) type 2 diabetes with A1c >= 7.0 after metformin trial, or (2) BMI >= 35 with a
documented weight-related comorbidity. Approvals last 12 months."""),
    dict(doc_id="SP-BUL-2024-003", state="SP", doc_type="provider_bulletin", topic="sglt2_pdl",
         drug_class="SGLT2", title="Provider Bulletin 2024-03: Preferred drug list update for SGLT2 inhibitors",
         published="2024-02-15", effective_date="2024-04-01", supersedes="",
         body="""Southport Medicaid updates its preferred drug list: empagliflozin and dapagliflozin remain
preferred. No changes are made to GLP-1 receptor agonist coverage. Southport continues to expand
enrollment following its 2024 eligibility simplification."""),
    dict(doc_id="FED-GUID-2023-02", state="ALL", doc_type="federal_guidance", topic="sdud_methodology",
         drug_class="", title="Federal guidance: State drug utilization data reporting and suppression",
         published="2023-03-01", effective_date="2023-03-01", supersedes="",
         body="""States report outpatient drug utilization quarterly. To protect privacy, counts below 11
are suppressed in public files. Recent quarters are subject to revision as late claims arrive, so
the two most recent quarters should be treated as provisional when comparing periods."""),
]


def write_policies(policy_dir: Path) -> None:
    policy_dir.mkdir(parents=True, exist_ok=True)
    for p in POLICIES:
        meta = "\n".join(f"{k}: {p[k]}" for k in
                         ("doc_id", "state", "doc_type", "topic", "drug_class", "title",
                          "published", "effective_date", "supersedes"))
        text = (f"---\n{meta}\nsynthetic: true\n---\n\n"
                f"> SYNTHETIC TEST DOCUMENT for the fictional state of "
                f"{STATES.get(p['state'], {}).get('name', 'all states')}. Not a real policy.\n\n"
                f"# {p['title']}\n\n{p['body'].strip()}\n")
        (policy_dir / f"{p['doc_id']}.md").write_text(text, encoding="utf-8")


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def build(out_dir: Path = SYNTHETIC_DIR, db_path: Path = DB_PATH) -> Path:
    """Create CSVs, the DuckDB database, and the policy corpus. Deterministic."""
    out_dir.mkdir(parents=True, exist_ok=True)
    enrollment, utilization, status = build_tables(random.Random(SEED))
    _write_csv(out_dir / "enrollment.csv", enrollment)
    _write_csv(out_dir / "drug_utilization.csv", utilization)
    _write_csv(out_dir / "reporting_status.csv", status)
    states = [{"state": k, "state_name": v["name"], "data_as_of": DATA_AS_OF} for k, v in STATES.items()]
    _write_csv(out_dir / "states.csv", states)
    write_policies(out_dir / "policies" if out_dir != SYNTHETIC_DIR else POLICY_DIR)

    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    for table in ("enrollment", "drug_utilization", "reporting_status", "states"):
        con.execute(f"CREATE TABLE {table} AS SELECT * FROM read_csv_auto(?, header=true)",
                    [str(out_dir / f"{table}.csv")])
    con.close()
    return db_path


if __name__ == "__main__":
    print(f"Built {build()}")
