"""SQL guardrails (D1.10): each layer is tested independently."""

import duckdb
import pytest

from medicaidiq.db import SQLRejected, validate_sql


@pytest.mark.parametrize("sql", [
    "DELETE FROM enrollment",
    "DROP TABLE enrollment",
    "UPDATE enrollment SET member_months = 0",
    "SELECT 1; DROP TABLE enrollment",
    "INSERT INTO states VALUES ('X','Y','2020-01-01')",
    "SELECT * FROM read_csv_auto('C:/secrets.csv')",
    "ATTACH 'other.db' AS o",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM pg_catalog.pg_tables",
    "",
])
def test_rejects_unsafe_sql(sql):
    with pytest.raises(SQLRejected):
        validate_sql(sql)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM enrollment WHERE state = 'EV'",
    "select state, sum(prescriptions) from drug_utilization group by state;",
    "WITH t AS (SELECT * FROM enrollment) SELECT COUNT(*) FROM t",
    "SELECT e.state FROM enrollment e JOIN states s ON e.state = s.state",
])
def test_accepts_read_only_sql(sql):
    assert validate_sql(sql)


def test_connection_is_read_only_even_if_validation_bypassed(db):
    with pytest.raises(duckdb.Error):
        db.con.execute("CREATE TABLE hacked AS SELECT 1")


def test_row_limit(db):
    res = db.query("SELECT * FROM drug_utilization", max_rows=5)
    assert len(res.rows) == 5 and res.truncated


def test_sql_error_becomes_observation(toolbox):
    from medicaidiq.memory import WorkingMemory
    mem = WorkingMemory("q")
    obs = toolbox.execute("run_sql", '{"sql": "SELECT nonexistent_col FROM enrollment"}', mem)
    assert "error" in obs and mem.failed_attempts and not mem.evidence
