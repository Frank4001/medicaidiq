"""Hybrid retrieval (Week 3): chunking, filters, ranking, MMR, adjacency, failure-mode guards."""

import numpy as np
import pytest

from medicaidiq.retrieval import PolicySearch, _group_regulation, load_chunks, mmr_select


@pytest.fixture(scope="module")
def search():
    return PolicySearch()


def test_chunks_unique_and_cover_both_corpora():
    chunks = load_chunks()
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))
    assert any(c.jurisdiction == "US" for c in chunks)          # real federal regulations
    assert any(c.jurisdiction == "EV" for c in chunks)          # synthetic state documents
    assert all(c.meta.get("url") for c in chunks)               # provenance on every chunk


def test_regulation_grouping_keeps_roman_numerals_with_parent():
    paras = ["(a) General rule.", "(i) first item", "(ii) second", "(b) Next rule.", "(c) Third.",
             "(d) D", "(e) E", "(f) F", "(g) G", "(h) H", "(i) Real top-level i."]
    labels = [label for label, _ in _group_regulation(paras)]
    assert labels == ["(a)", "(b)", "(c)", "(d)", "(e)", "(f)", "(g)", "(h)", "(i)"]


def test_public_snapshot_has_provenance():
    reg = next(c for c in load_chunks() if c.doc_id == "42-CFR-430.20")
    assert reg.meta["url"].startswith("https://www.ecfr.gov/") and len(reg.meta["sha256"]) == 64


def test_state_specific_question_retrieves_that_states_document(search):
    r = search.search("What clinical criteria does Eastvale require for GLP-1 prior authorization?", state="EV")
    ids = [p["chunk_id"] for p in r["passages"]]
    assert "EV-MAN-2024-PH#p1" in ids
    assert r["retrieval_quality"]["status"] == "ok"
    assert all(p["jurisdiction"] in ("EV", "US", "ALL") for p in r["passages"])


def test_jurisdiction_filter_prevents_cross_state_leakage(search):
    r = search.search("GLP-1 prior authorization criteria", state="NL", include_federal=False)
    assert r["passages"] and all(p["jurisdiction"] == "NL" for p in r["passages"])


def test_federal_regulation_found_semantically(search):
    r = search.search("Can a state plan amendment take effect before it is submitted?")
    assert any(p["doc_id"] == "42-CFR-430.20" for p in r["passages"])


def test_unknown_state_triggers_scope_warning_and_low_relevance(search):
    r = search.search("What are Ohio GLP-1 prior authorization criteria?", state="OH")
    assert "scope_warning" in r
    assert r["retrieval_quality"]["status"] == "out_of_scope"


def test_off_topic_query_flagged_low_relevance(search):
    r = search.search("What is the best pizza topping?")
    assert r["retrieval_quality"]["status"] == "low_relevance" and "warning" in r["retrieval_quality"]


def test_superseded_document_is_labelled(search):
    r = search.search("GLP-1 prior authorization start date June 2024", state="EV")
    old = [p for p in r["passages"] if p["doc_id"] == "EV-BUL-2024-007"]
    assert old and old[0]["superseded_by"] == "EV-BUL-2024-011"


def test_adjacent_expansion_brings_in_the_exception():
    """Mirrors the Checkpoint 3.1 telehealth finding: the rule ranks high, its exception doesn't."""
    from medicaidiq.retrieval import Chunk
    meta = {"jurisdiction": "US", "doc_type": "federal_guidance", "url": "x"}
    chunks = [
        Chunk("T#p1", "T", 0, "Telehealth", "p1",
              "A separate amendment is not needed when telehealth is reimbursed the same as face-to-face.", meta),
        Chunk("T#p2", "T", 1, "Telehealth", "p2",
              "However, a reimbursement amendment is required when components are paid differently.", meta),
        Chunk("X#p1", "X", 0, "Other", "p1", "Unrelated dental guidance text.", meta),
    ]
    r = PolicySearch(chunks=chunks, mode="bm25", k=1).search("Is a separate telehealth amendment needed")
    got = {p["chunk_id"]: p["selected_by"] for p in r["passages"]}
    assert got.get("T#p1") == "ranked" and got.get("T#p2") == "adjacent to T#p1"


def test_mmr_prefers_diverse_passages():
    q = np.array([1.0, 0.0])
    docs = np.array([[0.99, 0.14], [0.98, 0.2], [0.7, -0.71]])
    docs = docs / np.linalg.norm(docs, axis=1, keepdims=True)
    assert mmr_select(q, docs, k=2, lam=0.5) == [0, 2]   # skips the near-duplicate
    assert mmr_select(q, docs, k=2, lam=1.0) == [0, 1]   # pure relevance keeps it


def test_keyword_only_fallback_mode():
    s = PolicySearch(mode="bm25")
    r = s.search("EV-MAN-2024-PH prior authorization criteria")
    assert r["retrieval_quality"]["mode"] == "bm25" and r["passages"][0]["doc_id"].startswith("EV-")
