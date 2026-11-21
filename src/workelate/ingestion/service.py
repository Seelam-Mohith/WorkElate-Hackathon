"""Atomic daily ingestion. Only source metadata, day N, and feedback N-1 are read."""

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from workelate.config import Settings
from workelate.ingestion.errors import IngestionError
from workelate.ingestion.loader import canonical_json, read_json, read_jsonl
from workelate.ingestion.validator import validate
from workelate.schemas.feedback import Feedback
from workelate.schemas.records import ClientsAdapter, OrganizationsAdapter, RecordAdapter
from workelate.storage.database import connect_database, transaction
from workelate.storage.schema import RESET_TABLES
from workelate.storage.migrations import MEMORY_RESET_TABLES
from workelate.retrieval.schema import SEARCH_RESET_TABLES
from workelate.feedback.schema import LEARNING_RESET_TABLES
from workelate.forgetting.schema import FORGET_RESET_TABLES
from workelate.time_utils import normalized_utc

logger = logging.getLogger("workelate")


def database_path(settings: Settings) -> Path:
    path = (settings.runtime_dir / "workelate.sqlite3").resolve()
    # A configuration mistake must not create application files among source data.
    for source_dir in (settings.pack_dir / "data", settings.pack_dir / "answers", settings.pack_dir / "tools"):
        if path.is_relative_to(source_dir.resolve()):
            raise IngestionError("unsafe_runtime_directory")
    return path


def _sync_metadata(conn: sqlite3.Connection, data: Path) -> None:
    organizations = validate(OrganizationsAdapter, read_json(data / "people.json"), "people.json")
    clients = validate(ClientsAdapter, read_json(data / "clients.json"), "clients.json")
    seen_people, seen_clients = set(), set()
    for org_id, org in organizations.items():
        conn.execute("INSERT INTO organizations VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name", (org_id, org.name))
        for person in org.people:
            if person.id in seen_people:
                raise IngestionError("duplicate_person_id", source="people.json")
            seen_people.add(person.id)
            conn.execute("""INSERT INTO people VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET org_id=excluded.org_id, name=excluded.name,
                role=excluded.role, team=excluded.team, timezone=excluded.timezone""",
                (person.id, org_id, person.name, person.role, person.team, person.zone))
    for client in clients:
        if client.id in seen_clients:
            raise IngestionError("duplicate_client_id", source="clients.json")
        seen_clients.add(client.id)
        conn.execute("INSERT INTO clients VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name", (client.id, client.name))


def _store_record(conn, record, raw: dict, raw_text: str, day: int, line: int, source: str) -> None:
    existing = conn.execute("SELECT raw_json FROM records WHERE id = ?", (record.id,)).fetchone()
    if existing:
        if canonical_json(raw) != canonical_json(json.loads(existing[0])):
            raise IngestionError("conflicting_record_id", source=source, line=line)
        return
    try:
        conn.execute("""INSERT INTO records
            (id, org_id, record_type, occurred_at, original_at, author_id, client_id,
             visibility, body, source_day, source_line, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (record.id, record.org, record.type, normalized_utc(record.at), raw["at"], record.author,
             record.client, record.visibility, record.body, day, line, raw_text))
        if record.visibility == "restricted":
            conn.executemany("INSERT INTO record_readers VALUES (?, ?)",
                [(record.id, reader) for reader in sorted(set(record.readers))])
    except sqlite3.IntegrityError as exc:
        raise IngestionError("source_integrity_error", source=source, line=line) from exc


def _store_feedback(conn, feedback: Feedback, raw: dict, raw_text: str, day: int, line: int, source: str) -> None:
    if feedback.day != day:
        raise IngestionError("feedback_day_mismatch", source=source, line=line)
    fingerprint = hashlib.sha256(canonical_json(raw).encode("utf-8")).hexdigest()
    existing = conn.execute("SELECT fingerprint FROM feedback WHERE source_day=? AND source_line=?", (day, line)).fetchone()
    if existing:
        if existing[0] != fingerprint:
            raise IngestionError("conflicting_feedback", source=source, line=line)
        return
    try:
        conn.execute("""INSERT INTO feedback
            (source_day, source_line, fingerprint, person_id, feedback_type, words, meeting_id, topic, verdict, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (day, line, fingerprint, feedback.person, feedback.type, feedback.words,
             feedback.meeting, feedback.topic, feedback.verdict, raw_text))
    except sqlite3.IntegrityError as exc:
        raise IngestionError("source_integrity_error", source=source, line=line) from exc


def ingest_day(day: int, settings: Settings, *, llm_client=None, feedback_client=None) -> dict:
    if isinstance(day, bool) or not isinstance(day, int) or day <= 0:
        raise IngestionError("invalid_day")
    data = settings.pack_dir / "data"
    records_processed = feedback_processed = 0
    try:
        with connect_database(database_path(settings)) as conn, transaction(conn):
            if day == 1:
                # Transactional reset: a failed new run leaves the previous run intact.
                for table in (*FORGET_RESET_TABLES, *LEARNING_RESET_TABLES, *SEARCH_RESET_TABLES, *MEMORY_RESET_TABLES, *RESET_TABLES):
                    conn.execute(f"DELETE FROM {table}")
            elif conn.execute("SELECT 1 FROM ingestion_history WHERE day=?", (day - 1,)).fetchone() is None:
                raise IngestionError("previous_day_not_ingested")
            _sync_metadata(conn, data)
            source = data / "days" / f"day-{day:02d}.jsonl"
            for line, raw, text in read_jsonl(source):
                record = validate(RecordAdapter, raw, source.name, line)
                _store_record(conn, record, raw, text, day, line, source.name)
                records_processed += 1
            if day > 1:
                source = data / "feedback" / f"day-{day - 1:02d}.jsonl"
                for line, raw, text in read_jsonl(source):
                    feedback = validate(Feedback, raw, source.name, line)
                    _store_feedback(conn, feedback, raw, text, day - 1, line, source.name)
                    feedback_processed += 1
            from workelate.forgetting.service import apply_rules, process_forget_feedback
            process_forget_feedback(conn, day)
            apply_rules(conn)
            conn.execute("""INSERT INTO ingestion_history VALUES (?, 'completed', ?, ?, ?)
                ON CONFLICT(day) DO UPDATE SET status=excluded.status,
                records_processed=excluded.records_processed,
                feedback_processed=excluded.feedback_processed, completed_at=excluded.completed_at""",
                (day, records_processed, feedback_processed, normalized_utc(datetime.now(timezone.utc))))
    except IngestionError:
        raise
    except (sqlite3.Error, OSError) as exc:
        raise IngestionError("database_error") from exc
    logger.info("Completed ingestion day %d: %d records, %d feedback", day, records_processed, feedback_processed)
    from workelate.memory.service import process_memory

    extraction = process_memory(settings, through_day=day, client=llm_client)
    from workelate.feedback.service import process_feedback
    learning = process_feedback(settings, through_day=day, client=feedback_client)
    from workelate.retrieval.indexing import index_records
    indexing = index_records(settings, through_day=day)
    return {"ok": True, "day": day, "records_processed": records_processed,
            "feedback_processed": feedback_processed, "extraction": extraction, "indexing": indexing,
            "feedback_learning": learning}
