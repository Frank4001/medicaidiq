"""Deterministic analysis tools. The LLM never computes numbers itself (D1.2).

pre_post_analysis applies the Week 1 feedback responses:
  F1: excludes provisional (claims-lag) and missing quarters and says so; decomposes
      spending change into enrollment, utilization, and price.
  F2: effect size + bootstrap 95% CI + materiality threshold -> 4-quadrant verdict.
"""

from __future__ import annotations

import ast
import math
import operator
import random
from datetime import date

from .db import ReadOnlyDB

METRICS = {
    "rx_per_1000_mm": "Prescriptions per 1,000 member-months",
    "spend_pmpm": "Reimbursement per member per month (USD)",
    "spend_per_rx": "Reimbursement per prescription (USD)",
}


def _quarter_of(d: date) -> tuple[int, int]:
    return d.year, (d.month - 1) // 3 + 1


def _shift(yq: tuple[int, int], n: int) -> tuple[int, int]:
    idx = yq[0] * 4 + (yq[1] - 1) + n
    return idx // 4, idx % 4 + 1


def first_post_quarter(effective: date) -> tuple[tuple[int, int], str]:
    """Quarter-level data: a policy effective mid-quarter contaminates that quarter, so skip it."""
    yq = _quarter_of(effective)
    if effective.day == 1 and effective.month in (1, 4, 7, 10):
        return yq, f"Policy starts on the first day of {yq[0]}Q{yq[1]}; that quarter is the first post-period quarter."
    nxt = _shift(yq, 1)
    return nxt, (f"Policy starts mid-quarter ({effective.isoformat()}); {yq[0]}Q{yq[1]} is mixed and "
                 f"excluded, post-period starts {nxt[0]}Q{nxt[1]}.")


def quarterly_series(db: ReadOnlyDB, state: str, drug_class: str) -> dict[tuple[int, int], dict]:
    sql = """
    SELECT r.year, r.quarter, r.status, e.member_months,
           SUM(CASE WHEN NOT u.suppressed THEN u.prescriptions END) AS rx,
           SUM(CASE WHEN NOT u.suppressed THEN u.reimbursement_usd END) AS spend,
           SUM(CASE WHEN u.suppressed THEN 1 ELSE 0 END) AS suppressed_rows
    FROM reporting_status r
    LEFT JOIN enrollment e USING (state, year, quarter)
    LEFT JOIN drug_utilization u ON u.state = r.state AND u.year = r.year AND u.quarter = r.quarter
                                 AND u.drug_class = ?
    WHERE r.state = ?
    GROUP BY 1, 2, 3, 4 ORDER BY 1, 2"""
    res = db.query_trusted(sql, [drug_class.upper(), state.upper()])
    out = {}
    for y, q, status, mm, rx, spend, supp in res.rows:
        row = {"status": status, "member_months": mm, "rx": rx, "spend": spend, "suppressed_rows": supp}
        if status != "missing" and mm and rx is not None:
            row.update(rx_per_1000_mm=rx / mm * 1000, spend_pmpm=spend / mm, spend_per_rx=spend / rx)
        out[(y, q)] = row
    return out


def _bootstrap_pct_change(pre: list[float], post: list[float], n: int = 4000, seed: int = 7) -> tuple[float, float]:
    rng = random.Random(seed)
    draws = []
    for _ in range(n):
        a = [rng.choice(pre) for _ in pre]
        b = [rng.choice(post) for _ in post]
        draws.append((sum(b) / len(b)) / (sum(a) / len(a)) * 100 - 100)
    draws.sort()
    return draws[int(0.025 * n)], draws[int(0.975 * n) - 1]


def pre_post_analysis(db: ReadOnlyDB, state: str, drug_class: str, effective_date: str,
                      metric: str = "rx_per_1000_mm", window_quarters: int = 4,
                      materiality_pct: float = 5.0) -> dict:
    if metric not in METRICS:
        return {"error": f"metric must be one of {list(METRICS)}"}
    try:
        eff = date.fromisoformat(effective_date)
    except ValueError:
        return {"error": "effective_date must be YYYY-MM-DD"}
    series = quarterly_series(db, state, drug_class)
    if not series:
        return {"error": f"No data for state={state}. Available states: NL, EV, SP."}

    post_start, timing_note = first_post_quarter(eff)
    pre_q = [_shift(post_start, -i) for i in range(window_quarters, 0, -1)]
    if post_start != _quarter_of(eff):           # mid-quarter start: pre ends before the mixed quarter
        pre_q = [_shift(q, -1) for q in pre_q]
    post_q = [_shift(post_start, i) for i in range(window_quarters)]

    excluded, pre_vals, post_vals = [], [], []
    for label, quarters, bucket in (("pre", pre_q, pre_vals), ("post", post_q, post_vals)):
        for yq in quarters:
            row = series.get(yq)
            if row is None:
                excluded.append({"quarter": f"{yq[0]}Q{yq[1]}", "period": label, "reason": "outside data range"})
            elif row["status"] != "final" or metric not in row:
                excluded.append({"quarter": f"{yq[0]}Q{yq[1]}", "period": label,
                                 "reason": f"{row['status']} (incomplete or not reported)"})
            else:
                bucket.append(row[metric])

    fmt = lambda qs: [f"{y}Q{q}" for y, q in qs]
    base = {"state": state.upper(), "drug_class": drug_class.upper(), "metric": metric,
            "metric_label": METRICS[metric], "effective_date": effective_date, "timing_note": timing_note,
            "pre_quarters": fmt(pre_q), "post_quarters": fmt(post_q), "excluded_quarters": excluded}
    if len(pre_vals) < 2 or len(post_vals) < 2:
        return {**base, "status": "insufficient_data",
                "message": "Fewer than 2 complete quarters in the pre or post period; no conclusion possible."}

    pre_mean, post_mean = sum(pre_vals) / len(pre_vals), sum(post_vals) / len(post_vals)
    pct = post_mean / pre_mean * 100 - 100
    lo, hi = _bootstrap_pct_change(pre_vals, post_vals)
    significant = lo > 0 or hi < 0
    material = abs(pct) >= materiality_pct
    verdict = {(True, True): "significant_and_material", (True, False): "significant_but_immaterial",
               (False, True): "not_significant_possibly_material", (False, False): "no_meaningful_change"}
    return {**base, "status": "ok",
            "pre_mean": round(pre_mean, 3), "post_mean": round(post_mean, 3),
            "pct_change": round(pct, 1), "ci95_pct_change": [round(lo, 1), round(hi, 1)],
            "materiality_threshold_pct": materiality_pct,
            "verdict": verdict[(significant, material)],
            "n_quarters": {"pre": len(pre_vals), "post": len(post_vals)},
            "suppressed_rows_in_window": sum(series[q]["suppressed_rows"] or 0
                                             for q in pre_q + post_q if q in series),
            "interpretation_guardrail": "Pre/post association only; does not by itself establish that the policy caused the change."}


def spending_decomposition(db: ReadOnlyDB, state: str, drug_class: str,
                           start_quarter: str, end_quarter: str) -> dict:
    """Split the change in total spending into enrollment, utilization, and price (log shares)."""
    series = quarterly_series(db, state, drug_class)
    parse = lambda s: (int(s[:4]), int(s[-1]))
    try:
        a, b = series[parse(start_quarter)], series[parse(end_quarter)]
    except (KeyError, ValueError):
        return {"error": "Quarters must look like 2023Q2 and exist in the data."}
    for label, row in (("start", a), ("end", b)):
        if row["status"] != "final":
            return {"error": f"{label} quarter is {row['status']}; choose final quarters."}
    parts = {
        "enrollment": math.log(b["member_months"] / a["member_months"]),
        "utilization_per_member": math.log(b["rx_per_1000_mm"] / a["rx_per_1000_mm"]),
        "price_per_rx": math.log(b["spend_per_rx"] / a["spend_per_rx"]),
    }
    total = sum(parts.values())
    return {"state": state.upper(), "drug_class": drug_class.upper(),
            "start_quarter": start_quarter, "end_quarter": end_quarter,
            "spending_start_usd": round(a["spend"], 2), "spending_end_usd": round(b["spend"], 2),
            "total_spending_pct_change": round((math.exp(total) - 1) * 100, 1),
            "component_pct_change": {k: round((math.exp(v) - 1) * 100, 1) for k, v in parts.items()},
            "share_of_change_pct": {k: round(v / total * 100, 1) if total else 0.0 for k, v in parts.items()}}


def data_coverage(db: ReadOnlyDB, state: str) -> dict:
    res = db.query_trusted("SELECT year, quarter, status, note FROM reporting_status WHERE state = ? "
                           "ORDER BY year, quarter", [state.upper()])
    if not res.rows:
        return {"state": state.upper(), "available": False,
                "message": "No data for this state. Available states: NL, EV, SP."}
    by_status: dict[str, list[str]] = {}
    for y, q, status, _ in res.rows:
        by_status.setdefault(status, []).append(f"{y}Q{q}")
    supp = db.query_trusted("SELECT COUNT(*) FROM drug_utilization WHERE state = ? AND suppressed",
                            [state.upper()]).rows[0][0]
    return {"state": state.upper(), "available": True, "quarters_by_status": by_status,
            "suppressed_rows": supp,
            "guidance": "Compare only 'final' quarters; disclose provisional/missing quarters."}


# ---- safe calculator ------------------------------------------------------------------------
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}


def calculate(expression: str) -> dict:
    """Evaluate +-*/** arithmetic on numbers only (no names, no calls)."""
    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        raise ValueError("Only numbers and + - * / ** ( ) are allowed.")
    try:
        if len(expression) > 200:
            raise ValueError("Expression too long.")
        value = ev(ast.parse(expression.replace(",", ""), mode="eval"))
        return {"expression": expression, "result": round(value, 6)}
    except (ValueError, SyntaxError, ZeroDivisionError) as exc:
        return {"error": f"Cannot evaluate: {exc}"}
