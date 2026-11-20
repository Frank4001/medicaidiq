"""Fetch the PUBLIC external corpus (Week 3): federal Medicaid regulations + CMS dataset docs.

Sources (official, built for programmatic access):
  * eCFR API (www.ecfr.gov): Title 42 sections on state plan amendments, prescribed drugs,
    drug payment, and drug utilization review. Pinned to one eCFR version date.
  * data.medicaid.gov metastore: the official description of State Drug Utilization Data.

Medicaid.gov web pages are not used: they block automated clients (HTTP 403), and we
do not try to get around that.

Each document is saved as JSON with provenance: source URL, version date, retrieval
timestamp, and a SHA-256 hash of the text (reproducible, auditable snapshots). Snapshots
are committed, so reviewers don't need network access. Re-run: `medicaidiq fetch-public`.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from .paths import DATA_DIR

PUBLIC_DIR = DATA_DIR / "public"
ECFR_SECTIONS = {
    # section: (part, why it is in the corpus)
    "430.12": ("430", "How states submit state plans and plan amendments"),
    "430.20": ("430", "Effective dates of state plan amendments"),
    "440.120": ("440", "Definition of prescribed drugs"),
    "447.205": ("447", "Public notice before changing payment methods"),
    "447.256": ("447", "Effective dates for payment-method SPAs (gap found by the agent, cited from 430.20(b)(2))"),
    "447.512": ("447", "Payment limits for covered outpatient drugs"),
    "447.518": ("447", "State plan requirements for drug payment"),
    "456.703": ("456", "Drug use review (DUR) program requirements"),
    "456.705": ("456", "Prospective drug review, including point-of-sale screening"),
    "456.709": ("456", "Retrospective drug use review"),
}
UA = {"User-Agent": "MedicaidIQ-capstone/0.3 (academic research; python urllib)",
      "Accept-Encoding": "gzip"}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            import gzip
            data = gzip.decompress(data)
        return data


def ecfr_version_date() -> str:
    meta = json.loads(_get("https://www.ecfr.gov/api/versioner/v1/titles.json"))
    title42 = next(t for t in meta["titles"] if t["number"] == 42)
    return title42["up_to_date_as_of"]


def _text(el: ET.Element) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def fetch_ecfr_section(section: str, part: str, date: str) -> dict:
    url = (f"https://www.ecfr.gov/api/versioner/v1/full/{date}/title-42.xml"
           f"?part={part}&section={section}")
    root = ET.fromstring(_get(url))
    head = root.find(".//HEAD")
    title = _text(head) if head is not None else f"§ {section}"
    paras = [_text(p) for p in root.iter("P") if _text(p)]
    return {"doc_id": f"42-CFR-{section}", "title": f"42 CFR {title.lstrip('§ ').strip()}",
            "jurisdiction": "US", "doc_type": "statute_or_regulation",
            "source": "eCFR (Electronic Code of Federal Regulations)",
            "url": f"https://www.ecfr.gov/current/title-42/section-{section}",
            "version_date": date, "paragraphs": paras}


def fetch_sdud_description() -> dict:
    items = json.loads(_get("https://data.medicaid.gov/api/1/metastore/schemas/dataset/items"
                            "?show-reference-ids=false"))
    sdud = [x for x in items if re.fullmatch(r"State Drug Utilization Data \d{4}", x.get("title", ""))]
    latest = max(sdud, key=lambda x: x["title"])
    paras = [p.strip() for p in re.split(r"\n\s*\n|(?<=\.)\s+(?=Data descriptions)", latest["description"]) if p.strip()]
    return {"doc_id": "DMG-SDUD-DESCRIPTION", "title": f"data.medicaid.gov: {latest['title']} (dataset description)",
            "jurisdiction": "US", "doc_type": "federal_guidance", "source": "data.medicaid.gov metastore API",
            "url": f"https://data.medicaid.gov/dataset/{latest['identifier']}",
            "version_date": (latest.get("modified") or "")[:10], "paragraphs": paras}


def save(doc: dict, out_dir: Path) -> Path:
    doc["retrieved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    doc["sha256"] = hashlib.sha256("\n".join(doc["paragraphs"]).encode()).hexdigest()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{doc['doc_id']}.json"
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def fetch_all(out_dir: Path = PUBLIC_DIR) -> list[Path]:
    date = ecfr_version_date()
    paths = [save(fetch_ecfr_section(s, part, date), out_dir) for s, (part, _) in ECFR_SECTIONS.items()]
    paths.append(save(fetch_sdud_description(), out_dir))
    return paths
