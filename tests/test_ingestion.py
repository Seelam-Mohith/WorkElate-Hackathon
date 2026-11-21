"""Ingestion, historical integrity, rollback, source timing, and CLI persistence."""

import json
import sqlite3

import pytest
from pydantic import ValidationError

from conftest import record
from test_cli import invoke
from workelate.ingestion.errors import IngestionError
from workelate.ingestion.loader import read_jsonl
from workelate.ingestion.service import ingest_day
from workelate.schemas.feedback import Feedback
from workelate.schemas.records import RecordAdapter
from workelate.storage.database import connect_database


def rows(pack, table):
    with connect_database(pack.database, read_only=True) as conn:
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]


def snapshot(pack):
    return {table: rows(pack, table) for table in (
        "organizations", "people", "clients", "records", "record_readers", "feedback", "ingestion_history"
    )}


def test_metadata_and_database_initialization(pack):
    assert ingest_day(1, pack.settings) == {"ok": True, "day": 1, "records_processed": 1, "feedback_processed": 0}
    assert len(rows(pack, "organizations")) == 2
    people = {person["id"]: person for person in rows(pack, "people")}
    assert people["p1"]["org_id"] == "org-a"
    assert people["p2"]["team"] is None
    assert people["p2"]["timezone"] == "Europe/London"
    assert rows(pack, "clients") == [{"id": "client-1", "name": "Client One"}]
    assert rows(pack, "ingestion_history")[0]["status"] == "completed"
    with connect_database(pack.database) as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO record_readers VALUES ('missing', 'p1')")


@pytest.mark.parametrize("kind", ["mail", "chat", "doc", "sheet", "task", "event", "note", "notification"])
def test_every_type_preserves_full_source(pack, kind):
    source = record("r-typed", kind, unknown_future_field={"nested": ["value", 2]}, body="हैलो — source")
    pack.records(1, [source])
    ingest_day(1, pack.settings)
    stored = rows(pack, "records")[0]
    assert json.loads(stored["raw_json"]) == source
    assert stored["record_type"] == kind
    assert stored["original_at"] == source["at"]
    assert stored["occurred_at"] == "2026-11-02T10:00:00.000000Z"
    assert stored["source_day"] == stored["source_line"] == 1


def test_external_authors_and_null_clients(pack):
    pack.records(1, [record("r-ext", "mail", author="ext:client@example.com", client=None), record("r-sys", "notification", author="system")])
    ingest_day(1, pack.settings)
    assert len(rows(pack, "records")) == 2


def test_local_day_is_not_assumed_to_be_utc_day(pack):
    # 23:30 UTC on Nov 1 is Nov 2 in the author's Kolkata timezone.
    pack.records(1, [record(at="2026-11-01T23:30:00Z")])
    ingest_day(1, pack.settings)
    stored = rows(pack, "records")[0]
    assert stored["source_day"] == 1
    assert stored["original_at"] == "2026-11-01T23:30:00Z"


def test_versions_and_task_history_are_not_overwritten(pack):
    pack.records(1, [record("r-doc1", "doc"), record("r-sheet1", "sheet"), record("r-task1", "task")])
    pack.records(2, [record("r-doc2", "doc", version=2), record("r-sheet2", "sheet", version=2, cells={"A1": "99"}), record("r-task2", "task", status="done", change={"field": "status", "from": "todo", "to": "done"})])
    ingest_day(1, pack.settings)
    ingest_day(2, pack.settings)
    assert len(rows(pack, "records")) == 6


@pytest.mark.parametrize("field,value", [
    ("id", ""), ("id", "bad id"), ("org", ""), ("org", "unknown-org"),
    ("type", "unsupported"), ("visibility", "public"), ("at", "2026-11-02T10:00:00"),
    ("at", "2026-11-02T10:00:00+05:30"), ("body", 42), ("client", "unknown-client"),
    ("author", " "), ("readers", "p1"),
])
def test_invalid_common_fields_are_rejected(pack, field, value):
    pack.records(1, [record(**{field: value})])
    with pytest.raises(IngestionError) as error:
        ingest_day(1, pack.settings)
    assert error.value.code in {"invalid_source_data", "source_integrity_error"}
    assert error.value.line == 1
    assert rows(pack, "records") == []
    assert rows(pack, "ingestion_history") == []


@pytest.mark.parametrize("field", ["id", "org", "type", "at", "author", "client", "visibility", "body"])
def test_required_common_fields(pack, field):
    source = record()
    del source[field]
    pack.records(1, [source])
    with pytest.raises(IngestionError, match="invalid_source_data"):
        ingest_day(1, pack.settings)


@pytest.mark.parametrize("kind,field,value", [
    ("mail", "to", "p1"), ("mail", "subject", 2), ("chat", "channel", []),
    ("doc", "version", 0), ("doc", "version", True), ("sheet", "cells", {"A1": 4}),
    ("task", "due", "2026-02-30"), ("task", "status", "finished"),
    ("task", "change", {"field": "owner", "to": "p2"}),
    ("task", "change", {"field": "due", "from": "2026-11-01", "to": "tomorrow"}),
    ("task", "change", {"field": "status", "from": "todo", "to": "oops"}),
    ("event", "start", "2026-11-02"), ("event", "attendees", "p1"),
    ("notification", "to", [1]), ("note", "event_id", ""),
])
def test_invalid_type_fields(kind, field, value):
    with pytest.raises((ValidationError, ValueError)):
        RecordAdapter.validate_python(record(record_type=kind, **{field: value}))


def test_jsonl_blank_lines_and_line_provenance(pack):
    source = pack.root / "data/days/day-01.jsonl"
    source.write_text("\n" + json.dumps(record()) + "\n\n", encoding="utf-8")
    assert next(read_jsonl(source))[0] == 2
    ingest_day(1, pack.settings)
    assert rows(pack, "records")[0]["source_line"] == 2


@pytest.mark.parametrize("text", ["{bad json}", "[]", '{"id":"one","id":"two"}', '{"value":NaN}', '{"value":1e999}'])
def test_malformed_json_is_not_skipped(pack, text):
    (pack.root / "data/days/day-01.jsonl").write_text(text, encoding="utf-8")
    with pytest.raises(IngestionError, match="invalid_source_json"):
        ingest_day(1, pack.settings)
    assert rows(pack, "records") == []


def test_incremental_ingestion_delays_feedback_and_is_idempotent(pack):
    for day in (1, 2, 3):
        result = ingest_day(day, pack.settings)
        assert result["records_processed"] == 1
        assert result["feedback_processed"] == (0 if day == 1 else 1)
        assert len(rows(pack, "records")) == day
        assert len(rows(pack, "feedback")) == day - 1
    ingest_day(3, pack.settings)
    assert len(rows(pack, "records")) == 3
    assert len(rows(pack, "feedback")) == 2
    assert {row["source_day"] for row in rows(pack, "feedback")} == {1, 2}


def test_identical_duplicate_records_do_not_duplicate_storage(pack):
    source = record()
    pack.records(1, [source, dict(reversed(list(source.items())))])
    pack.records(2, [source])
    assert ingest_day(1, pack.settings)["records_processed"] == 2
    ingest_day(2, pack.settings)
    assert len(rows(pack, "records")) == 1
    assert rows(pack, "records")[0]["source_day"] == 1


def test_conflicting_ids_rollback_whole_day(pack):
    ingest_day(1, pack.settings)
    original = snapshot(pack)
    pack.records(2, [record("new-first"), record("r-day-1", body="Conflicting source")])
    with pytest.raises(IngestionError, match="conflicting_record_id") as error:
        ingest_day(2, pack.settings)
    assert error.value.line == 2
    assert snapshot(pack) == original


def test_conflicting_ids_in_single_file(pack):
    pack.records(1, [record(), record(body="conflict")])
    with pytest.raises(IngestionError, match="conflicting_record_id"):
        ingest_day(1, pack.settings)
    assert rows(pack, "records") == []


def test_failed_day_one_reset_preserves_previous_run(pack):
    ingest_day(1, pack.settings)
    ingest_day(2, pack.settings)
    original = snapshot(pack)
    pack.records(1, [record("new-run"), {"invalid": True}])
    with pytest.raises(IngestionError):
        ingest_day(1, pack.settings)
    assert snapshot(pack) == original


def test_day_one_starts_fresh_run(pack):
    for day in (1, 2, 3):
        ingest_day(day, pack.settings)
    ingest_day(1, pack.settings)
    assert len(rows(pack, "records")) == 1
    assert rows(pack, "feedback") == []
    assert [row["day"] for row in rows(pack, "ingestion_history")] == [1]


def test_no_read_ahead_or_answer_ingestion(pack):
    # These files must never be opened by ingestion, including Day 1 feedback.
    for relative in ("data/feedback/day-01.jsonl", "data/days/day-02.jsonl", "data/tasks/day-01.json", "answers/day-01.json"):
        path = pack.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("BROKEN FUTURE OR NONINPUT FILE", encoding="utf-8")
    ingest_day(1, pack.settings)
    assert len(rows(pack, "records")) == 1
    assert rows(pack, "feedback") == []


def test_missing_previous_feedback_rolls_back_records_and_metadata(pack):
    ingest_day(1, pack.settings)
    original = snapshot(pack)
    (pack.root / "data/feedback/day-01.jsonl").unlink()
    people_path = pack.root / "data/people.json"
    people = json.loads(people_path.read_text(encoding="utf-8"))
    people["org-a"]["people"][0]["name"] = "Changed Name"
    people_path.write_text(json.dumps(people), encoding="utf-8")
    with pytest.raises(IngestionError, match="missing_source_file"):
        ingest_day(2, pack.settings)
    assert snapshot(pack) == original


@pytest.mark.parametrize("relative", ["data/people.json", "data/clients.json", "data/days/day-01.jsonl"])
def test_missing_required_source_cli(pack, tmp_path, relative):
    (pack.root / relative).unlink()
    result, payload = invoke(["ingest", "--day", "1"], tmp_path, extra_env={
        "R3_PACK": str(pack.root), "WORKELATE_RUNTIME_DIR": str(pack.settings.runtime_dir),
    })
    assert result.returncode == 4
    assert payload["error"] == "missing_source_file"
    assert payload["source"] == relative.split("/")[-1]


def test_cli_process_persistence_and_real_counts(pack, tmp_path):
    for day in (1, 2, 3):
        result, payload = invoke(["ingest", "--day", str(day)], tmp_path, entrypoint=day == 2, extra_env={
            "R3_PACK": str(pack.root), "WORKELATE_RUNTIME_DIR": str(pack.settings.runtime_dir), "WORKELATE_LOG_LEVEL": "INFO",
        })
        assert result.returncode == 0
        assert payload == {"ok": True, "day": day, "records_processed": 1, "feedback_processed": int(day > 1)}
        assert "Completed ingestion" in result.stderr
        assert "INFO" not in result.stdout
        assert "Private source text" not in result.stderr
    assert len(rows(pack, "records")) == 3
    assert len(rows(pack, "feedback")) == 2


def test_ingest_cwd_fallback(pack):
    result, payload = invoke(["ingest", "--day", "1"], pack.root)
    assert result.returncode == 0
    assert payload["records_processed"] == 1
    assert (pack.root / ".workelate/workelate.sqlite3").is_file()


@pytest.mark.parametrize("day", [0, -1, True, "1"])
def test_service_validates_day(pack, day):
    with pytest.raises(IngestionError, match="invalid_day"):
        ingest_day(day, pack.settings)


def test_missing_previous_day_is_explicit(pack):
    with pytest.raises(IngestionError, match="previous_day_not_ingested"):
        ingest_day(2, pack.settings)


def test_invalid_metadata_timezone(pack):
    path = pack.root / "data/people.json"
    people = json.loads(path.read_text(encoding="utf-8"))
    people["org-a"]["people"][0]["zone"] = "Not/A_Timezone"
    path.write_text(json.dumps(people), encoding="utf-8")
    with pytest.raises(IngestionError, match="invalid_source_data"):
        ingest_day(1, pack.settings)


@pytest.mark.parametrize("kind", ["preference", "correction", "verdict", "forget"])
def test_feedback_types_are_stored_without_applying_them(pack, kind):
    source = {"day": 1, "person": "p1", "type": kind, "words": "Correction or preference to store", "future": "preserved"}
    if kind in {"correction", "verdict"}:
        source["meeting"] = "event-1"
    if kind == "verdict":
        source.update(topic="topic", verdict="ignored")
    pack.feedback(1, [source, source])
    ingest_day(1, pack.settings)
    before = rows(pack, "records")
    ingest_day(2, pack.settings)
    ingest_day(2, pack.settings)
    feedback = rows(pack, "feedback")
    assert len(feedback) == 2  # Distinct source lines retained, even if content matches.
    assert feedback[0]["fingerprint"] == feedback[1]["fingerprint"]
    assert json.loads(feedback[0]["raw_json"]) == source
    assert rows(pack, "records")[0] == before[0]
    assert len(rows(pack, "people")) == 3


@pytest.mark.parametrize("changes", [
    {"type": "bad"}, {"words": " "}, {"type": "correction"},
    {"type": "verdict", "meeting": "event-1"}, {"day": 0},
])
def test_feedback_required_fields(changes):
    with pytest.raises(ValidationError):
        Feedback.model_validate({"day": 1, "person": "p1", "type": "preference", "words": "Preference", **changes})


def test_feedback_conflict_rolls_back(pack):
    ingest_day(1, pack.settings)
    ingest_day(2, pack.settings)
    original = snapshot(pack)
    pack.feedback(1, [{"day": 1, "person": "p1", "type": "preference", "words": "changed"}])
    with pytest.raises(IngestionError, match="conflicting_feedback"):
        ingest_day(2, pack.settings)
    assert snapshot(pack) == original


def test_feedback_wrong_day_is_rejected(pack):
    ingest_day(1, pack.settings)
    pack.feedback(1, [{"day": 2, "person": "p1", "type": "preference", "words": "future"}])
    with pytest.raises(IngestionError, match="feedback_day_mismatch"):
        ingest_day(2, pack.settings)
    assert len(rows(pack, "records")) == 1


def test_runtime_directory_cannot_be_source_data(pack):
    from dataclasses import replace

    with pytest.raises(IngestionError, match="unsafe_runtime_directory"):
        ingest_day(1, replace(pack.settings, runtime_dir=pack.root / "data/days"))
    assert not (pack.root / "data/days/workelate.sqlite3").exists()


def test_invalid_record_cli_does_not_leak_body(pack, tmp_path):
    pack.records(1, [record(at="Private source text; invalid timestamp")])
    result, payload = invoke(["ingest", "--day", "1"], tmp_path, extra_env={
        "R3_PACK": str(pack.root), "WORKELATE_RUNTIME_DIR": str(pack.settings.runtime_dir),
    })
    assert result.returncode == 4
    assert payload == {"error": "invalid_source_data", "command": "ingest", "source": "day-01.jsonl", "line": 1}
    assert "Private source text" not in result.stdout + result.stderr
