"""Real-state (Illinois) pipeline: offline transforms, provisional quarters, and human approval of policy dates."""

import csv
import json
from datetime import date

import duckdb

from medicaidiq import real_data
from medicaidiq.memory import KnowledgeStore


def _fixture(tmp_path):
    base = tmp_path / "il"
    base.mkdir()
    sdud = [  # one final quarter, one suppressed row, one latest (provisional) quarter
        dict(drug_class="GLP1", matched="OZEMPIC", utilization_type="MCOU", state="IL", ndc="00169413013", year=2025,
             quarter=4, suppression_used="false", product_name="OZEMPIC 1M", units_reimbursed="10.5",
             number_of_prescriptions="1200", total_amount_reimbursed="1500000.0", medicaid_amount_reimbursed="1"),
        dict(drug_class="GLP1", matched="OZEMPIC", utilization_type="FFSU", state="IL", ndc="00169413013", year=2025,
             quarter=4, suppression_used="true", product_name="OZEMPIC 1M", units_reimbursed="",
             number_of_prescriptions="", total_amount_reimbursed="", medicaid_amount_reimbursed=""),
        dict(drug_class="GLP1", matched="OZEMPIC", utilization_type="MCOU", state="IL", ndc="00169413013", year=2026,
             quarter=1, suppression_used="false", product_name="OZEMPIC 1M", units_reimbursed="5",
             number_of_prescriptions="700", total_amount_reimbursed="900000.0", medicaid_amount_reimbursed="1"),
    ]
    with (base / "sdud_il.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(sdud[0]))
        w.writeheader()
        w.writerows(sdud)
    enr = [{"reporting_period": f"{y}{m:02d}", "preliminary_or_updated": pu, "final_report": "Y",
            "total_medicaid_enrollment": v, "total_medicaid_and_chip_enrollment": "", "total_adult_medicaid_enrollment": ""}
           for y, m, pu, v in [(2025, 10, "U", "1000"), (2025, 11, "U", "1000"), (2025, 12, "U", "1000"),
                               (2025, 12, "P", "9999"),   # preliminary rows are ignored
                               (2026, 1, "U", "900"), (2026, 2, "U", "900"), (2026, 3, "U", "900")]]
    with (base / "enrollment_il.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(enr[0]))
        w.writeheader()
        w.writerows(enr)
    (base / "manifest.json").write_text(json.dumps({"sdud": {"2026": {"modified": "2026-07-13T00:00:00"}},
                                                    "pdl": []}), encoding="utf-8")
    return base


def test_transforms_keep_suppression_and_split_ffs_mco(tmp_path):
    base = _fixture(tmp_path)
    util = real_data.utilization_rows(base)
    supp = [r for r in util if r["suppressed"]]
    assert len(supp) == 1 and supp[0]["prescriptions"] is None          # suppressed is NULL, never 0
    assert {r["utilization_type"] for r in util} == {"FFS", "MCO"}
    enr = real_data.enrollment_rows(base)
    assert [(r["year"], r["quarter"], r["member_months"]) for r in enr] == [(2025, 4, 3000), (2026, 1, 2700)]


def test_latest_quarter_is_provisional(tmp_path):
    base = _fixture(tmp_path)
    util, enr = real_data.utilization_rows(base), real_data.enrollment_rows(base)
    status = {(r["year"], r["quarter"]): r["status"] for r in real_data.status_rows(util, enr, base)}
    assert status == {(2025, 4): "final", (2026, 1): "provisional"}


def test_extend_database_adds_real_state(tmp_path):
    base = _fixture(tmp_path)
    db = tmp_path / "t.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE states (state VARCHAR, state_name VARCHAR, data_as_of DATE)")
    con.execute("CREATE TABLE enrollment (state VARCHAR, year BIGINT, quarter BIGINT, member_months BIGINT)")
    con.execute("CREATE TABLE drug_utilization (state VARCHAR, year BIGINT, quarter BIGINT, drug_class VARCHAR, "
                "product_name VARCHAR, ndc VARCHAR, prescriptions BIGINT, units BIGINT, reimbursement_usd DOUBLE, "
                "suppressed BOOLEAN)")
    con.execute("CREATE TABLE reporting_status (state VARCHAR, year BIGINT, quarter BIGINT, status VARCHAR, "
                "note VARCHAR)")
    con.execute("INSERT INTO states VALUES ('NL', 'Northland', '2026-01-15')")
    con.close()
    info = real_data.extend_database(db, base)
    assert info["provisional"] == ["2026Q1"]
    con = duckdb.connect(str(db), read_only=True)
    assert con.execute("SELECT data_source FROM states WHERE state='NL'").fetchone()[0].startswith("synthetic")
    assert con.execute("SELECT data_source FROM states WHERE state='IL'").fetchone()[0].startswith("real")
    assert con.execute("SELECT units FROM drug_utilization WHERE year=2025 AND NOT suppressed").fetchone()[0] == 10.5
    con.close()


def test_candidate_dates_need_human_approval(tmp_path):
    ks = KnowledgeStore(path=tmp_path / "ks.json", today=date(2026, 11, 21))
    key = "IL:glp1_coverage:effective_date"
    ks.add_candidate(key, "2026-10-01", "IL-PDL-2026-10-01", "preferred_drug_list", "2026-10-01", "label")
    ks.add_candidate(key, "2026-10-20", "IL-PDL-2026-10-20", "preferred_drug_list", "2026-10-20", "pdf header")
    r = ks.resolve(key)
    assert r.status == "needs_review" and r.value is None and len(r.conflicts) == 2   # never auto-chosen
    assert ks.approve(key, "2026-10-01", "reviewer")
    r = ks.resolve(key)
    assert r.status == "resolved" and r.value == "2026-10-01"


def test_rejected_candidate_is_not_used(tmp_path):
    ks = KnowledgeStore(path=tmp_path / "ks.json", today=date(2026, 11, 21))
    key = "IL:glp1_coverage:effective_date"
    ks.add_candidate(key, "2026-10-20", "IL-PDL-2026-10-20", "preferred_drug_list", "2026-10-20", "")
    assert ks.reject(key, "2026-10-20", "reviewer")
    assert ks.resolve(key).status == "unknown"


def test_approval_log_persists(tmp_path):
    path = tmp_path / "approvals.json"
    real_data.record_approval("IL:x:effective_date", "2026-10-01", "reviewer", "checked PDF", path=path)
    real_data.record_approval("IL:x:effective_date", "2026-10-20", "reviewer", "changed mind", path=path)
    entries = real_data.approvals(path)
    assert len(entries) == 1 and entries[0]["value"] == "2026-10-20"     # one decision per key
