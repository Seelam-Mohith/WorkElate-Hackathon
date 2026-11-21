"""Milestone B: stable entity resolution, calendar interpretation, and fusion."""

import json
from datetime import date

from conftest import record
from workelate.ingestion.service import ingest_day
from workelate.reasoning.classifier import classify
from workelate.retrieval.entity_resolution import resolve
from workelate.retrieval.hybrid import retrieve, rrf
from workelate.retrieval.temporal import interpret_time
from workelate.storage.database import connect_database


def test_entities_aliases_ids_titles_and_geographic_context(pack):
    pack.records(1, [record("task", "task", task_id="t-co-01", title="Product feed fix"),
                     record("event", "event", author="p2", title="Client check-in"), record("doc", "doc", title="Launch plan")])
    ingest_day(1, pack.settings)
    with connect_database(pack.database, read_only=True) as conn:
        resolved = resolve(conn, "p1", "Who owns CO product feed fix?", "2026-11-03T00:00:00Z")
        assert {item.id for item in resolved.entities} >= {"client-1", "t-co-01"}
        assert not resolved.ambiguous
        assert "client-1" in {item.id for item in resolve(conn, "p1", "London account", "2026-11-03T00:00:00Z").entities}
        assert "doc-1" in {item.id for item in resolve(conn, "p1", "Launch plan", "2026-11-03T00:00:00Z").entities}
        assert "event-1" in {item.id for item in resolve(conn, "p1", "event-1", "2026-11-03T00:00:00Z").entities}
        assert "p2" in {item.id for item in resolve(conn, "p1", "Person Two", "2026-11-03T00:00:00Z").entities}


def test_ambiguous_first_names_and_task_references(pack):
    path = pack.root / "data/people.json"
    directory = json.loads(path.read_text(encoding="utf-8"))
    directory["org-a"]["people"][0]["name"] = "Arjun One"
    directory["org-a"]["people"][1]["name"] = "Arjun Two"
    path.write_text(json.dumps(directory), encoding="utf-8")
    pack.records(1, [record("a", "task", title="Monthly report"), record("b", "task", task_id="task-2", title="Ops report")])
    ingest_day(1, pack.settings)
    with connect_database(pack.database, read_only=True) as conn:
        resolved = resolve(conn, "p1", "What did Arjun promise?", "2026-11-03T00:00:00Z")
        assert {item.id for item in resolved.ambiguous} == {"p1", "p2"}
        # Full names retain separate identities.
        assert not resolve(conn, "p1", "Arjun One", "2026-11-03T00:00:00Z").ambiguous


def test_private_future_and_cross_org_entities_are_not_resolved(pack):
    pack.records(1, [record("private", "task", title="Secret payroll", visibility="private"),
                     record("future", "task", task_id="future-task", title="Future forecast", at="2026-11-03T10:00:00Z"),
                     record("foreign", org="org-b", author="q1", body="Foreign project")])
    ingest_day(1, pack.settings)
    with connect_database(pack.database, read_only=True) as conn:
        result = resolve(conn, "p2", "Secret payroll future-task", "2026-11-02T23:00:00Z")
        assert not [entity for entity in result.entities if entity.kind == "task"]
        assert resolve(conn, "unknown", "Person Two", "2026-11-03T00:00:00Z").entities == []


def test_local_relative_dates_and_strict_historical_cutoffs():
    at = "2026-11-04T23:30:00Z"
    assert interpret_time("What is due tomorrow?", at, "Asia/Kolkata").due_date == "2026-11-06"
    assert interpret_time("What is due tomorrow?", at, "Europe/London").due_date == "2026-11-05"
    monday = interpret_time("Who owned it on Monday?", at, "Asia/Kolkata")
    assert monday.as_of.isoformat() == "2026-11-02T18:29:59.999999+00:00"
    since = interpret_time("What changed yesterday?", at, "Asia/Kolkata")
    assert since.since.isoformat() == "2026-11-03T18:30:00+00:00"
    assert since.as_of.isoformat() == "2026-11-04T18:29:59.999999+00:00"
    before = interpret_time("Who owned it before 2026-11-03?", at, "Europe/London")
    assert before.as_of.isoformat() == "2026-11-02T23:59:59.999999+00:00"


def test_rrf_prioritizes_structured_channels_and_deduplicates():
    assert rrf([(4, ["fact", "fact"]), (1, ["lexical", "fact"]), (1, ["semantic"])])[0] == "fact"
    assert len(rrf([(1, ["a", "a"]), (1, ["a", "b"])])) == 2


def test_hybrid_structured_history_expansion_and_no_hidden_evidence(pack):
    pack.records(1, [record("old", "task", title="Product feed")])
    pack.records(2, [record("updated", "task", title="Product feed", owner="p2", at="2026-11-03T10:00:00Z", change={"field": "owner", "from": "p1", "to": "p2"}),
                     record("private", body="Product feed", visibility="private")])
    ingest_day(1, pack.settings)
    ingest_day(2, pack.settings)
    with connect_database(pack.database, read_only=True) as conn:
        question = "What changed for task-1 owner since Monday?"
        times = interpret_time(question, "2026-11-03T18:00:00Z", "Europe/London")
        resolution = resolve(conn, "p2", question, times.cutoff)
        evidence = retrieve(conn, question, "p2", times, resolution, classify(question))
        assert {r["id"] for r in evidence.records} >= {"old", "updated"}
        assert "private" not in {r["id"] for r in evidence.records}
        assert evidence.facts[0]["value"] == "p2"
