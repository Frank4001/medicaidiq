"""Deterministic analysis tools recover the planted ground truth (see synth.py)."""

from datetime import date

from medicaidiq import stats


def test_eastvale_prior_auth_reduces_glp1(db):
    r = stats.pre_post_analysis(db, "EV", "GLP1", "2024-07-01")
    assert r["status"] == "ok"
    assert -23 < r["pct_change"] < -14            # planted x0.82
    assert r["ci95_pct_change"][1] < 0
    assert r["verdict"] == "significant_and_material"


def test_northland_coverage_expansion_increases_glp1(db):
    r = stats.pre_post_analysis(db, "NL", "GLP1", "2024-01-01")
    assert 30 < r["pct_change"] < 45              # planted x1.35
    assert r["verdict"] == "significant_and_material"


def test_control_drug_class_shows_no_meaningful_change(db):
    r = stats.pre_post_analysis(db, "EV", "SGLT2", "2024-07-01")
    assert abs(r["pct_change"]) < 5
    assert r["verdict"] in {"no_meaningful_change", "significant_but_immaterial"}


def test_missing_quarter_is_excluded_and_disclosed(db):
    r = stats.pre_post_analysis(db, "SP", "GLP1", "2024-07-01")
    assert any(e["quarter"] == "2024Q3" for e in r["excluded_quarters"])


def test_provisional_quarters_are_excluded(db):
    r = stats.pre_post_analysis(db, "EV", "GLP1", "2025-01-01")
    excluded = {e["quarter"] for e in r["excluded_quarters"]}
    assert {"2025Q3", "2025Q4"} <= excluded


def test_insufficient_data(db):
    r = stats.pre_post_analysis(db, "EV", "GLP1", "2025-10-01")
    assert r["status"] == "insufficient_data"


def test_mid_quarter_policy_skips_mixed_quarter():
    q, note = stats.first_post_quarter(date(2024, 6, 1))
    assert q == (2024, 3) and "mixed" in note
    q, _ = stats.first_post_quarter(date(2024, 7, 1))
    assert q == (2024, 3)


def test_spending_growth_driven_by_enrollment_not_utilization(db):
    r = stats.spending_decomposition(db, "SP", "GLP1", "2023Q4", "2025Q2")
    comp = r["component_pct_change"]
    assert r["total_spending_pct_change"] > 0
    assert comp["enrollment"] > 10 and comp["enrollment"] > comp["utilization_per_member"]


def test_decomposition_refuses_provisional_quarters(db):
    assert "error" in stats.spending_decomposition(db, "EV", "GLP1", "2023Q1", "2025Q4")


def test_unknown_state(db):
    assert stats.data_coverage(db, "ZZ")["available"] is False


def test_calculator_is_exact_and_safe():
    assert stats.calculate("9.11 - 9.9")["result"] == -0.79
    assert stats.calculate("(24880 / 10 + 220 - 110) * 1234")["result"] == 3205932
    assert "error" in stats.calculate("__import__('os').system('dir')")
    assert "error" in stats.calculate("1/0")
