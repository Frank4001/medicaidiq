"""Read-only data access with layered SQL guardrails (design decision D1.10).

Defense in depth, each layer independent of the others:
  1. Static validation: one statement, must start with SELECT/WITH, no write/DDL/file keywords.
  2. Table allowlist: only approved tables may be referenced.
  3. Read-only connection: DuckDB opened with read_only=True, so writes fail even if 1-2 miss.
  4. Row limit and statement timeout.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path

import duckdb

from .paths import DB_PATH

ALLOWED_TABLES = {"enrollment", "drug_utilization", "reporting_status", "states"}
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|attach|detach|copy|export|import|"
    r"install|load|pragma|set|call|grant|revoke|vacuum|checkpoint|read_csv\w*|read_parquet|read_json\w*|"
    r"glob|read_text|read_blob)\b",
    re.IGNORECASE,
)
MAX_ROWS = 200
TIMEOUT_S = 10.0

SCHEMA_DOC = """\
Tables (synthetic data mirroring CMS State Drug Utilization Data; states are FICTIONAL):

states(state VARCHAR, state_name VARCHAR, data_as_of DATE)
  - state codes: NL = Northland, EV = Eastvale, SP = Southport

enrollment(state VARCHAR, year INT, quarter INT, member_months BIGINT)
  - member_months = sum of months of enrollment in the quarter (denominator for rates/PMPM)

drug_utilization(state VARCHAR, year INT, quarter INT, drug_class VARCHAR, product_name VARCHAR,
                 ndc VARCHAR, prescriptions BIGINT, units BIGINT, reimbursement_usd DOUBLE,
                 suppressed BOOLEAN)
  - drug_class in ('GLP1', 'SGLT2', 'INSULIN')
  - suppressed = TRUE means the count was < 11 and is NULL (privacy suppression); never treat as 0

reporting_status(state VARCHAR, year INT, quarter INT, status VARCHAR, note VARCHAR)
  - status in ('final', 'provisional', 'missing'). Provisional quarters are incomplete (claims lag);
    missing quarters have no data. Check this table before comparing periods.
"""


class SQLRejected(ValueError):
    """Raised when a query fails validation. The message is shown to the agent as an observation."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]
    truncated: bool

    def to_dict(self) -> dict:
        return {"columns": self.columns, "rows": self.rows, "row_count": len(self.rows),
                "truncated": self.truncated}


def validate_sql(sql: str) -> str:
    """Return a cleaned single SELECT statement or raise SQLRejected with a fix-it hint."""
    cleaned = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.DOTALL).strip().rstrip(";").strip()
    if not cleaned:
        raise SQLRejected("Empty query.")
    if ";" in cleaned:
        raise SQLRejected("Only one statement is allowed; remove ';'.")
    if not re.match(r"^(select|with)\b", cleaned, re.IGNORECASE):
        raise SQLRejected("Only read-only SELECT queries are allowed.")
    bad = FORBIDDEN.search(cleaned)
    if bad:
        raise SQLRejected(f"Keyword '{bad.group(0)}' is not allowed in read-only queries.")
    referenced = {t.lower() for t in re.findall(r"\b(?:from|join)\s+([A-Za-z_][\w.]*)", cleaned,
                                                 flags=re.IGNORECASE)}
    cte_names = {c.lower() for c in re.findall(r"\b([A-Za-z_]\w*)\s+as\s*\(", cleaned, re.IGNORECASE)}
    unknown = referenced - ALLOWED_TABLES - cte_names
    if unknown:
        raise SQLRejected(f"Unknown or unapproved table(s): {sorted(unknown)}. "
                          f"Allowed: {sorted(ALLOWED_TABLES)}.")
    return cleaned


class ReadOnlyDB:
    def __init__(self, db_path: Path = DB_PATH):
        if not Path(db_path).exists():
            raise FileNotFoundError(f"{db_path} not found. Run `medicaidiq build-data` first.")
        self.con = duckdb.connect(str(db_path), read_only=True)
        self._lock = threading.Lock()

    def query(self, sql: str, params: list | None = None, max_rows: int = MAX_ROWS) -> QueryResult:
        """Validated query for agent-written SQL."""
        return self._run(validate_sql(sql), params, max_rows)

    def query_trusted(self, sql: str, params: list | None = None) -> QueryResult:
        """Internal queries written in this codebase (still on the read-only connection)."""
        return self._run(sql, params, max_rows=100_000)

    def _run(self, sql: str, params, max_rows: int) -> QueryResult:
        with self._lock:
            timer = threading.Timer(TIMEOUT_S, self.con.interrupt)
            timer.start()
            try:
                cur = self.con.execute(sql, params or [])
                cols = [d[0] for d in cur.description]
                rows = cur.fetchmany(max_rows + 1)
            except duckdb.InterruptException as exc:
                raise SQLRejected(f"Query exceeded {TIMEOUT_S:.0f}s timeout.") from exc
            except duckdb.Error as exc:
                raise SQLRejected(f"SQL error: {str(exc).splitlines()[0]}") from exc
            finally:
                timer.cancel()
        truncated = len(rows) > max_rows
        rows = [[_jsonable(v) for v in r] for r in rows[:max_rows]]
        return QueryResult(cols, rows, truncated)

    def close(self) -> None:
        self.con.close()


def _jsonable(v):
    if isinstance(v, float):
        return round(v, 4)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v
