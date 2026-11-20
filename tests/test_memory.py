"""Long-term memory: supersession, conflict reconciliation, and update triggers (Week 2 feedback)."""

from datetime import date

from medicaidiq.memory import KnowledgeStore, WorkingMemory


def _doc(doc_id, doc_type, published, effective, supersedes="", state="EV", topic="pa"):
    return dict(doc_id=doc_id, doc_type=doc_type, published=published, effective_date=effective,
                supersedes=supersedes, state=state, topic=topic)


def _store(tmp_path, **kw):
    return KnowledgeStore(tmp_path / "ks.json", **kw)


def test_explicit_supersession(tmp_path):
    ks = _store(tmp_path)
    ks.ingest_document(_doc("B1", "provider_bulletin", "2024-05-15", "2024-06-01"))
    ks.ingest_document(_doc("B2", "provider_bulletin", "2024-06-10", "2024-07-01", supersedes="B1"))
    r = ks.resolve("EV:pa:effective_date")
    assert r.status == "resolved" and r.value == "2024-07-01" and r.source_doc == "B2"
    assert r.superseded[0]["source_doc"] == "B1"


def test_higher_authority_wins_and_conflict_is_disclosed(tmp_path):
    ks = _store(tmp_path)
    ks.ingest_document(_doc("SPA", "state_plan_amendment", "2023-11-20", "2024-01-01"))
    ks.ingest_document(_doc("MAN", "provider_manual", "2024-02-01", "2024-02-01"))
    r = ks.resolve("EV:pa:effective_date")
    assert r.value == "2024-01-01"
    assert r.conflicts and r.conflicts[0]["source_doc"] == "MAN"
    assert "Disclose" in r.explanation


def test_equal_authority_disagreement_is_not_resolved(tmp_path):
    ks = _store(tmp_path)
    ks.ingest_document(_doc("M1", "provider_manual", "2024-01-01", "2024-03-01"))
    ks.ingest_document(_doc("M2", "provider_manual", "2024-01-05", "2024-04-01"))
    r = ks.resolve("EV:pa:effective_date")
    assert r.status == "conflicted" and r.value is None


def test_stale_fact_triggers_review(tmp_path):
    ks = _store(tmp_path, review_days=30, today=date(2026, 1, 1))
    ks.ingest_document(_doc("B1", "provider_bulletin", "2024-05-15", "2024-06-01"))
    ks.facts[0].last_verified = "2025-01-01"
    assert ks.resolve("EV:pa:effective_date").status == "needs_review"


def test_agent_interpretation_requires_human_approval(tmp_path):
    ks = _store(tmp_path)
    ks.add_interpretation("EV:pa:effective_date", "2024-08-01", "inferred from claims pattern")
    assert ks.resolve("EV:pa:effective_date").status == "unknown"   # provisional is not evidence
    assert ks.approve("EV:pa:effective_date", "2024-08-01", reviewer="analyst")
    assert ks.resolve("EV:pa:effective_date").value == "2024-08-01"


def test_persistence_round_trip(tmp_path):
    ks = _store(tmp_path)
    ks.ingest_document(_doc("B1", "provider_bulletin", "2024-05-15", "2024-06-01"))
    ks.save()
    assert _store(tmp_path).resolve("EV:pa:effective_date").value == "2024-06-01"


def test_built_store_handles_planted_traps(built_data):
    ks = KnowledgeStore()
    ev = ks.resolve("EV:glp1_prior_auth:effective_date")
    nl = ks.resolve("NL:glp1_coverage:effective_date")
    assert ev.value == "2024-07-01" and any(s["source_doc"] == "EV-BUL-2024-007" for s in ev.superseded)
    assert nl.value == "2024-01-01" and nl.conflicts


def test_working_memory_evidence_and_scratchpad():
    m = WorkingMemory("q")
    eid = m.add_evidence("calculate", "x", {"result": 12.5})
    m.update_slots(state="EV", drug_class=None)
    m.record_failure("run_sql: bad column")
    assert eid == "E1" and 12.5 in m.numbers_in_evidence()
    pad = m.scratchpad()
    assert '"state": "EV"' in pad and "drug_class" not in pad and "bad column" in pad
