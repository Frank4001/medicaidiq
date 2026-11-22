"""Real public data for one state: Illinois (IL), GLP-1 focus, SGLT2 and insulin as controls.

Two halves:

* fetch_illinois()  (network, run rarely: `medicaidiq fetch-real`)
    - CMS State Drug Utilization Data (SDUD) from the data.medicaid.gov datastore API, IL only,
      filtered by product name (brand and generic names below), FFS and MCO rows kept.
    - Monthly Medicaid enrollment (data.medicaid.gov "PI" dataset), IL only.
    - Illinois HFS Preferred Drug List (PDL) spreadsheets, current and archived versions
      (hfs.illinois.gov allows crawling in robots.txt; we fetch slowly, >= 2 s apart).
  Raw snapshots are saved under data/real/illinois/ with a manifest (URL, dataset version,
  retrieval time, SHA-256), so builds are offline and reproducible.

* extend_database() / write_policy_docs() / candidate_facts()  (offline, part of build-data)
    - Adds IL rows to the same four tables as the synthetic states (identical schema, plus
      `utilization_type` and `data_source` columns).
    - Writes one policy document per PDL version whose GLP-1/SGLT2/anti-obesity sections changed.
    - Proposes candidate policy dates as *provisional* facts. Policy dates for a real state are
      never inferred from claims and never auto-approved: a human approves them
      (data/real/illinois/approvals.json, `medicaidiq approve-fact`).

Known limits (stated to users by the agent): amounts are pre-rebate; ~95% of GLP-1 claims are
managed care; suppressed rows (< 11 prescriptions) are lower bounds, not zeros; the latest
quarter is incomplete; product names in SDUD are truncated to 10 characters.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

from .paths import DATA_DIR

REAL_DIR = DATA_DIR / "real" / "illinois"
REAL_POLICY_DIR = REAL_DIR / "policies"
APPROVALS_PATH = REAL_DIR / "approvals.json"
UA = {"User-Agent": "MedicaidIQ-capstone/1.1 (academic research; polite client)"}
STATE, STATE_NAME = "IL", "Illinois"
DATA_SOURCE = "real: CMS State Drug Utilization Data + Medicaid enrollment (data.medicaid.gov); HFS PDL"

SDUD_DATASETS = {  # data.medicaid.gov dataset ids (version: "updated July 2026" releases)
    2022: "200c2cba-e58d-4a95-aa60-14b99736808d",
    2023: "d890d3a9-6b00-43fd-8b31-fcba4c8e2909",
    2024: "61729e5a-7aa8-448c-8903-ba3e0cd0ea3c",
    2025: "158a1baa-5506-400a-8ec3-97756f0b0536",
    2026: "2957a7f9-9a15-453e-9afd-3bbdcbac8fd3",
}
ENROLLMENT_DATASET = "6165f45b-ca93-5bb5-9d06-db29c692a360"   # Medicaid & CHIP enrollment (PI dataset)

# SDUD product names are truncated to 10 characters; patterns are matched with SQL LIKE '%x%'.
# Fixed-ratio combination products (e.g. insulin + GLP-1: Soliqua, Xultophy; SGLT2 + metformin)
# are deliberately excluded so each class means one mechanism.
CLASS_PATTERNS = {
    "GLP1": ["OZEMPIC", "WEGOVY", "RYBELSUS", "MOUNJARO", "ZEPBOUND", "TRULICITY", "VICTOZA", "SAXENDA",
             "BYETTA", "BYDUREON", "ADLYXIN", "LIRAGLUTID", "EXENATIDE", "SEMAGLUTID", "TIRZEPATID"],
    "SGLT2": ["JARDIANCE", "FARXIGA", "INVOKANA", "STEGLATRO", "DAPAGLIFLO", "BRENZAVVY"],
    "INSULIN": ["INSULIN", "LANTUS", "BASAGLAR", "TOUJEO", "SEMGLEE", "REZVOGLAR", "LEVEMIR", "TRESIBA",
                "HUMALOG", "NOVOLOG", "ADMELOG", "LYUMJEV", "FIASP", "APIDRA", "HUMULIN", "NOVOLIN",
                "AFREZZA", "MYXREDLIN"],
}
EXCLUDE = re.compile(r"SOLIQUA|XULTOPHY|SYNJARDY|XIGDUO|GLYXAMBI|TRIJARDY|INVOKAMET|QTERN|STEGLUJAN|SEGLUROMET")

HFS = "https://hfs.illinois.gov/content/dam/soi/en/web/hfs"
# (version date as labelled on the HFS archive page, path). Effective dates are NOT taken from here:
# see candidate_facts() and the human approval step.
PDL_VERSIONS = [
    ("2022-01-01", "sitecollectiondocuments/01012022pdlfinal.xlsx"),
    ("2022-04-01", "sitecollectiondocuments/04012022pdlfinalupdated.xlsx"),
    ("2022-07-01", "sitecollectiondocuments/07012022pdlfinal.xlsx"),
    ("2022-10-01", "sitecollectiondocuments/pdl10012022.xlsx"),
    ("2023-01-01", "sitecollectiondocuments/pdl01012023.xlsx"),
    ("2023-07-01", "sitecollectiondocuments/pdl07012023.xlsx"),
    ("2023-10-01", "sitecollectiondocuments/pdl10012023.xlsx"),
    ("2024-01-01", "sitecollectiondocuments/pdl01012024.xlsx"),
    ("2025-03-13", "sitecollectiondocuments/pdl03132025.xlsx"),
    ("2025-05-01", "sitecollectiondocuments/pdl05012025.xlsx"),
    ("2025-07-01", "sitecollectiondocuments/pdl07012025.xlsx"),
    ("2025-10-01", "sitecollectiondocuments/pdl10012025.xlsx"),
    ("2025-10-24", "sitecollectiondocuments/pdl10242025.xlsx"),
    ("2026-01-01", "sitecollectiondocuments/pdl01012026.xlsx"),
    ("2026-01-27", "medicalproviders/pharmacy/documents/pdl01272026.xlsx"),
    ("2026-04-01", "medicalproviders/pharmacy/documents/pdl04012026.xlsx"),
    ("2026-04-24", "medicalproviders/pharmacy/documents/pdl04242026.xlsx"),
    ("2026-07-01", "medicalproviders/pharmacy/documents/pdl07012026.xlsx"),
    ("2026-10-01", "medicalproviders/pharmacy/documents/pdl10012026.xlsx"),
    ("2026-10-20", "medicalproviders/pharmacy/documents/pdl10202026.xlsx"),
]
PDL_CLASS_KEYS = ("GLP-1", "SGLT", "ANTI-OBESITY")
STATUSES = ("PREFERRED", "PREFERRED_WITH_PA", "NON_PREFERRED")
PROVISIONAL_MONTHS = 6   # a quarter ending < 6 months before the data release is treated as incomplete


# ============================================================================ network fetch
def _get(url: str, timeout: int = 120, tries: int = 4) -> bytes:
    """GET with retries and exponential backoff (a live fetch died on a transient DNS failure)."""
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt == tries - 1 or (isinstance(exc, urllib.error.HTTPError) and exc.code < 500):
                raise
            time.sleep(5 * 3 ** attempt)
    raise RuntimeError("unreachable")


def _datastore(dataset: str, conditions: list[tuple[str, str, str]], limit: int = 500,
               cache: Path | None = None) -> list[dict]:
    """Paged datastore query. With `cache`, a finished query is saved so an interrupted fetch resumes."""
    if cache is not None:
        key = hashlib.sha256(json.dumps([dataset, conditions]).encode()).hexdigest()[:20]
        hit = cache / f"{key}.json"
        if hit.exists():
            return json.loads(hit.read_text(encoding="utf-8"))
        rows = _datastore(dataset, conditions, limit)
        cache.mkdir(parents=True, exist_ok=True)
        hit.write_text(json.dumps(rows), encoding="utf-8")
        return rows
    rows, offset = [], 0
    while True:
        params = {"limit": str(limit), "offset": str(offset), "count": "true"}
        for i, (prop, op, val) in enumerate(conditions):
            params.update({f"conditions[{i}][property]": prop, f"conditions[{i}][operator]": op,
                           f"conditions[{i}][value]": val})
        url = f"https://data.medicaid.gov/api/1/datastore/query/{dataset}/0?" + urllib.parse.urlencode(params)
        d = json.loads(_get(url))
        rows += d.get("results", [])
        offset += limit
        if offset >= int(d.get("count", 0)):
            return rows
        time.sleep(0.5)


def _metastore_modified(dataset: str) -> str:
    try:
        d = json.loads(_get(f"https://data.medicaid.gov/api/1/metastore/schemas/dataset/items/{dataset}"))
        return d.get("modified", "")
    except Exception:  # noqa: BLE001 - provenance is best effort
        return ""


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def fetch_illinois(out: Path = REAL_DIR, log=print) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest = {"retrieved_at": now, "user_agent": UA["User-Agent"], "sdud": {}, "enrollment": {}, "pdl": []}

    # 1. SDUD
    seen, rows = set(), []
    for year, ds in SDUD_DATASETS.items():
        manifest["sdud"][str(year)] = {"dataset": ds, "modified": _metastore_modified(ds),
                                       "url": f"https://data.medicaid.gov/api/1/datastore/query/{ds}/0"}
        for drug_class, pats in CLASS_PATTERNS.items():
            for pat in pats:
                for r in _datastore(ds, [("state", "=", STATE), ("product_name", "like", f"%{pat}%")],
                                    cache=out / ".cache"):
                    k = (r["utilization_type"], r["ndc"], r["year"], r["quarter"])
                    if k in seen or EXCLUDE.search(r["product_name"] or ""):
                        continue
                    seen.add(k)
                    rows.append({**r, "drug_class": drug_class, "matched": pat})
        log(f"SDUD {year}: {len(rows)} rows so far")
    cols = ["drug_class", "matched", "utilization_type", "state", "ndc", "year", "quarter", "suppression_used",
            "product_name", "units_reimbursed", "number_of_prescriptions", "total_amount_reimbursed",
            "medicaid_amount_reimbursed"]
    _write(out / "sdud_il.csv", rows, cols)
    manifest["sdud"]["rows"] = len(rows)
    manifest["sdud"]["sha256"] = _sha((out / "sdud_il.csv").read_bytes())

    # 2. Enrollment
    enr = _datastore(ENROLLMENT_DATASET, [("state_abbreviation", "=", STATE)])
    ecols = ["reporting_period", "preliminary_or_updated", "final_report", "total_medicaid_enrollment",
             "total_medicaid_and_chip_enrollment", "total_adult_medicaid_enrollment"]
    _write(out / "enrollment_il.csv", enr, ecols)
    manifest["enrollment"] = {"dataset": ENROLLMENT_DATASET, "modified": _metastore_modified(ENROLLMENT_DATASET),
                              "rows": len(enr), "sha256": _sha((out / "enrollment_il.csv").read_bytes())}
    log(f"Enrollment: {len(enr)} monthly rows")

    # 3. PDL versions (parsed sections only; raw spreadsheets are not committed)
    history = {}
    for version, path in PDL_VERSIONS:
        url = f"{HFS}/{path}"
        time.sleep(2.0)
        try:
            raw = _get(url)
        except Exception as exc:  # noqa: BLE001
            manifest["pdl"].append({"version": version, "url": url, "error": str(exc)})
            continue
        sections = parse_pdl_xlsx(raw)
        manifest["pdl"].append({"version": version, "url": url, "sha256": _sha(raw), "parsed": bool(sections)})
        if sections:
            history[version] = sections
        log(f"PDL {version}: {'parsed' if sections else 'unreadable'}")
    (out / "pdl_history.json").write_text(json.dumps(history, indent=1), encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def parse_pdl_xlsx(raw: bytes) -> dict:
    """Return {class heading: {"note": str, "drugs": [[drug, form, status], ...]}} for the classes we track."""
    import io
    import warnings

    import openpyxl

    warnings.filterwarnings("ignore", module="openpyxl")
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    if not wb.worksheets:   # some HFS workbooks only open in full mode
        wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)
    if not wb.worksheets:
        return {}
    out, cls = {}, None
    for r in wb.worksheets[0].iter_rows(values_only=True):
        r = ["" if c is None else str(c).strip() for c in r]
        if not any(r):
            continue
        if r[0]:
            cls = r[0]
        if not cls or not any(k in cls.upper() for k in PDL_CLASS_KEYS) or len(r) < 3 or not r[1]:
            continue
        head, _, note = cls.partition("\n")
        head = re.sub(r"\s+", " ", head).strip()
        sec = out.setdefault(head, {"note": " ".join(note.split()), "drugs": []})
        status = next((c for c in r[3:] if c in STATUSES), "")
        sec["drugs"].append([r[1], r[2], status])
    return out


def _write(path: Path, rows: list[dict], cols: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ============================================================================ offline build
def available(base: Path = REAL_DIR) -> bool:
    return (base / "sdud_il.csv").exists() and (base / "enrollment_il.csv").exists()


def _num(x: str) -> float | None:
    try:
        return float(x) if x not in ("", None) else None
    except ValueError:
        return None


def _release_date(base: Path) -> date:
    m = json.loads((base / "manifest.json").read_text(encoding="utf-8")) if (base / "manifest.json").exists() else {}
    dates = [v.get("modified", "")[:10] for k, v in m.get("sdud", {}).items() if isinstance(v, dict)]
    dates = [d for d in dates if d]
    return date.fromisoformat(max(dates)) if dates else date(2026, 7, 13)


def utilization_rows(base: Path = REAL_DIR) -> list[dict]:
    out = []
    with (base / "sdud_il.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            supp = str(r["suppression_used"]).lower() == "true"
            out.append({"state": STATE, "year": int(r["year"]), "quarter": int(r["quarter"]),
                        "drug_class": r["drug_class"], "product_name": r["product_name"], "ndc": r["ndc"],
                        "prescriptions": None if supp else _num(r["number_of_prescriptions"]),
                        "units": None if supp else _num(r["units_reimbursed"]),
                        "reimbursement_usd": None if supp else _num(r["total_amount_reimbursed"]),
                        "suppressed": supp,
                        "utilization_type": {"FFSU": "FFS", "MCOU": "MCO"}.get(r["utilization_type"],
                                                                               r["utilization_type"])})
    return out


def enrollment_rows(base: Path = REAL_DIR) -> list[dict]:
    """Quarterly member-months = sum of the three monthly enrollment counts (updated 'U' rows only)."""
    months: dict[tuple[int, int], float] = {}
    with (base / "enrollment_il.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["preliminary_or_updated"] != "U":
                continue
            v = _num(r["total_medicaid_enrollment"]) or _num(r["total_medicaid_and_chip_enrollment"])
            if v is None:
                continue
            p = r["reporting_period"]
            months[(int(p[:4]), int(p[4:6]))] = v
    quarters: dict[tuple[int, int], list[float]] = {}
    for (y, m), v in months.items():
        quarters.setdefault((y, (m - 1) // 3 + 1), []).append(v)
    return [{"state": STATE, "year": y, "quarter": q, "member_months": int(sum(vs))}
            for (y, q), vs in sorted(quarters.items()) if len(vs) == 3]


def status_rows(util: list[dict], enr: list[dict], base: Path = REAL_DIR) -> list[dict]:
    release = _release_date(base)
    have_util = {(r["year"], r["quarter"]) for r in util}
    have_enr = {(r["year"], r["quarter"]) for r in enr}
    out = []
    for y, q in sorted(have_util):
        end = date(y, q * 3, 28)
        months_after = (release.year - end.year) * 12 + release.month - end.month
        if (y, q) not in have_enr:
            status, note = "missing", "No complete enrollment for this quarter; rates cannot be computed."
        elif months_after < PROVISIONAL_MONTHS:
            status = "provisional"
            note = (f"Latest quarter in the {release.isoformat()} SDUD release; state reporting is incomplete "
                    "and CMS revises it later. Do not read a drop here as a real decline.")
        else:
            status, note = "final", "Real CMS SDUD; pre-rebate amounts; suppressed rows are lower bounds."
        out.append({"state": STATE, "year": y, "quarter": q, "status": status, "note": note})
    return out


def extend_database(db_path: Path, base: Path = REAL_DIR) -> dict:
    """Add Illinois to the DuckDB tables built from the synthetic CSVs."""
    import duckdb

    util = utilization_rows(base)
    enr = enrollment_rows(base)
    stat = status_rows(util, enr, base)
    con = duckdb.connect(str(db_path))
    try:
        con.execute("ALTER TABLE drug_utilization ADD COLUMN utilization_type VARCHAR DEFAULT 'ALL'")
        con.execute("ALTER TABLE drug_utilization ALTER units TYPE DOUBLE")   # real units are fractional
        con.execute("ALTER TABLE states ADD COLUMN data_source VARCHAR DEFAULT "
                    "'synthetic: fictional state with planted effects (known answers)'")
        con.executemany("INSERT INTO drug_utilization (state, year, quarter, drug_class, product_name, ndc, "
                        "prescriptions, units, reimbursement_usd, suppressed, utilization_type) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [[r[k] for k in ("state", "year", "quarter", "drug_class", "product_name", "ndc",
                                         "prescriptions", "units", "reimbursement_usd", "suppressed",
                                         "utilization_type")] for r in util])
        con.executemany("INSERT INTO enrollment VALUES (?, ?, ?, ?)",
                        [[r["state"], r["year"], r["quarter"], r["member_months"]] for r in enr])
        con.executemany("INSERT INTO reporting_status VALUES (?, ?, ?, ?, ?)",
                        [[r["state"], r["year"], r["quarter"], r["status"], r["note"]] for r in stat])
        con.execute("INSERT INTO states (state, state_name, data_as_of, data_source) VALUES (?, ?, ?, ?)",
                    [STATE, STATE_NAME, _release_date(base).isoformat(), DATA_SOURCE])
    finally:
        con.close()
    return {"utilization_rows": len(util), "enrollment_quarters": len(enr),
            "provisional": [f"{r['year']}Q{r['quarter']}" for r in stat if r["status"] == "provisional"]}


# ---------------------------------------------------------------------------- policy documents
def _section_text(head: str, sec: dict) -> str:
    groups = {s: sorted({f"{d} ({f})" if f else d for d, f, st in sec["drugs"] if st == s}) for s in STATUSES}
    parts = [f"Drug class {head}."]
    labels = {"PREFERRED": "Preferred", "PREFERRED_WITH_PA": "Preferred with prior authorization",
              "NON_PREFERRED": "Non-preferred"}
    for s in STATUSES:
        if groups[s]:
            parts.append(f"{labels[s]}: {', '.join(groups[s])}.")
    if sec.get("note"):
        parts.append(f"Class note printed on the PDL: \"{sec['note']}\"")
    return " ".join(parts)


def write_policy_docs(base: Path = REAL_DIR, out: Path = REAL_POLICY_DIR) -> list[str]:
    """One document per PDL version whose tracked sections changed. Effective dates are left blank:
    the knowledge store gets them only through candidate facts + human approval."""
    hist_path = base / "pdl_history.json"
    if not hist_path.exists():
        return []
    history = json.loads(hist_path.read_text(encoding="utf-8"))
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    urls = {p["version"]: p for p in manifest.get("pdl", [])}
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("IL-PDL-*.md"):
        old.unlink()
    written, prev_text, prev_id = [], None, ""
    for version in sorted(history):
        secs = history[version]
        paras = [_section_text(h, s) for h, s in sorted(secs.items())]
        text = "\n\n".join(paras)
        if text == prev_text:
            continue
        doc_id = f"IL-PDL-{version}"
        src = urls.get(version, {})
        front = {"doc_id": doc_id, "state": STATE, "doc_type": "preferred_drug_list", "topic": "glp1_coverage",
                 "drug_class": "GLP1", "title": f"Illinois Medicaid Preferred Drug List, version labelled {version} "
                 "(GLP-1, SGLT2 and anti-obesity sections)", "published": version, "effective_date": "",
                 "supersedes": prev_id, "synthetic": "false", "source_url": src.get("url", ""),
                 "sha256": src.get("sha256", "")}
        intro = (f"Source: Illinois Department of Healthcare and Family Services (HFS), Medicaid Preferred Drug "
                 f"List, version labelled {version} on the HFS website ({src.get('url', '')}). Real public "
                 "document; only the GLP-1, SGLT2 and anti-obesity sections are reproduced here. This PDL is "
                 "published by HFS; this document does not establish whether or how managed care plans, which "
                 "pay about 95% of Illinois GLP-1 prescriptions, apply it, so verify plan-level rules. The "
                 "clinical prior authorization criteria are not part of the PDL.")
        body = "\n\n".join([intro] + paras)
        fm = "\n".join(f"{k}: {v}" for k, v in front.items())
        (out / f"{doc_id}.md").write_text(f"---\n{fm}\n---\n\n# {front['title']}\n\n{body}\n", encoding="utf-8")
        written.append(doc_id)
        prev_text, prev_id = text, doc_id
    return written


# ---------------------------------------------------------------------------- policy dates (HITL)
def candidate_facts(base: Path = REAL_DIR) -> list[dict]:
    """Candidate effective dates proposed from documents, for a human to approve or reject.

    Only one GLP-1 policy change is in scope: the anti-obesity class note restricting Wegovy and
    Zepbound coverage first appears in the PDL version labelled 2026-10-01. The PDF header of the
    current list reads 'Effective October 20, 2026', and the archived 10/01 PDF link serves that same
    file, so the date is genuinely ambiguous and must be decided by a reviewer.
    """
    path = base / "candidate_facts.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def approvals(path: Path = APPROVALS_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def record_approval(key: str, value: str, reviewer: str, note: str = "", path: Path = APPROVALS_PATH) -> dict:
    entries = [a for a in approvals(path) if a["key"] != key]
    entry = {"key": key, "value": value, "reviewer": reviewer, "approved_on": date.today().isoformat(),
             "note": note}
    entries.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    return entry
