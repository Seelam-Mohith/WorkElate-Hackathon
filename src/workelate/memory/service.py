"""Incremental trusted extraction with independent, recoverable per-record writes."""

import json
import logging
import math
import time

from workelate.memory.llm_extractor import build_messages, persist_output, validate_output
from workelate.memory.store import identity, now
from workelate.memory.structured_extractor import extract, refresh_artifact_changes
from workelate.reasoning.llm_client import LLMError, client_from_config
from workelate.storage.database import connect_database, transaction
from workelate.time_utils import normalized_utc

VERSION = "memory-v1"
logger = logging.getLogger("workelate")


def _status(conn, record_id, kind, status, error=None, attempted=False):
    run_id = identity(record_id, VERSION, kind)
    conn.execute("""INSERT INTO extraction_runs
        (id,record_id,extractor_version,kind,status,attempts,completed_at,error_message) VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(record_id,extractor_version,kind) DO UPDATE SET status=excluded.status,
        attempts=extraction_runs.attempts+excluded.attempts,
        completed_at=excluded.completed_at,error_message=excluded.error_message""",
        (run_id, record_id, VERSION, kind, status, int(attempted), now() if status in {"completed", "not_needed"} else None, error))
    return run_id


def _completed(conn, record_id, kind):
    row = conn.execute("SELECT status FROM extraction_runs WHERE record_id=? AND extractor_version=? AND kind=?", (record_id, VERSION, kind)).fetchone()
    return row is not None and row[0] in {"completed", "not_needed"}


def _eligible(record):
    return record["type"] in {"mail", "chat", "doc", "note"} or (
        record["type"] == "task" and record["change"]["field"] == "comment")


def _audience(conn, record):
    if record["visibility"] == "team":
        return {row[0] for row in conn.execute("SELECT id FROM people WHERE org_id=?", (record["org"],))}
    if record["visibility"] == "private":
        return {record["author"]}
    return set(record["readers"])


def _unchanged_document(conn, record):
    if record["type"] != "doc":
        return False
    for row in conn.execute("""SELECT r.raw_json FROM records r JOIN extraction_runs e ON e.record_id=r.id
        WHERE r.org_id=? AND r.record_type='doc' AND r.occurred_at<=? AND r.id<>?
        AND NOT EXISTS (SELECT 1 FROM forgotten_records x WHERE x.record_id=r.id)
        AND e.kind='semantic' AND e.extractor_version=? AND e.status='completed'""",
        (record["org"], normalized_utc(record["at"]), record["id"], VERSION)):
        previous = json.loads(row[0])
        if previous["doc_id"] == record["doc_id"] and previous["body"] == record["body"] and _audience(conn, record) <= _audience(conn, previous):
            return True
    return False


def _semantic(conn, record, client, config):
    if _completed(conn, record["id"], "semantic"):
        return
    if not _eligible(record):
        _status(conn, record["id"], "semantic", "not_needed")
        return
    from workelate.privacy.injection import instruction_text
    if instruction_text(record["body"]):
        _status(conn, record["id"], "semantic", "not_needed", "embedded_instruction")
        return
    if client is None:
        from workelate.memory.explicit_language import extract_explicit
        explicit = extract_explicit(conn, record)
        if explicit:
            output = validate_output(conn, record, json.dumps(explicit))
            with transaction(conn):
                persist_output(conn, record, output)
        _status(conn, record["id"], "semantic", "pending", "provider_not_configured")
        return
    if len(json.dumps(record)) > config.max_source_chars:
        _status(conn, record["id"], "semantic", "pending", "source_too_large")
        return
    if _unchanged_document(conn, record):
        _status(conn, record["id"], "semantic", "not_needed", "unchanged_document")
        return
    messages = build_messages(conn, record)
    for attempt in range(config.attempts):
        run_id = _status(conn, record["id"], "semantic", "pending", attempted=True)
        started = time.monotonic()
        response = None
        error = None
        try:
            response = client.complete(messages)
            if any(value is not None and (type(value) is not int or value < 0)
                   for value in (response.input_tokens, response.output_tokens)):
                raise LLMError("invalid_provider_usage")
            if response.cost_usd is not None and (not isinstance(response.cost_usd, (int, float)) or
                    not math.isfinite(response.cost_usd) or response.cost_usd < 0):
                raise LLMError("invalid_provider_usage")
            output = validate_output(conn, record, response.content)
            # Validate the whole result before writing any knowledge. All claims
            # from this response and its completion marker commit together.
            with transaction(conn):
                persist_output(conn, record, output)
                _status(conn, record["id"], "semantic", "completed")
        except LLMError as exc:
            error = exc
        except TimeoutError:
            error = LLMError("provider_timeout", retryable=True)
        except Exception:
            error = LLMError("semantic_extraction_error")
        elapsed = int((time.monotonic() - started) * 1000)
        valid_usage = response is not None and (error is None or error.code != "invalid_provider_usage")
        with transaction(conn):
            conn.execute("""INSERT INTO llm_usage
                (extraction_run_id,model,input_tokens,output_tokens,cost_usd,cost_basis,latency_ms,outcome)
                VALUES (?,?,?,?,?,?,?,?)""",
                (run_id, response.model if response else client.model,
                 response.input_tokens if valid_usage else None,
                 response.output_tokens if valid_usage else None,
                 response.cost_usd if valid_usage else None, response.cost_basis if valid_usage else "unknown",
                 elapsed, error.code if error else "completed"))
            if error:
                _status(conn, record["id"], "semantic", "failed", error.code)
        if error is None or not error.retryable or attempt + 1 == config.attempts:
            break
        time.sleep(min(0.1 * (2 ** attempt), 0.4))


def process_memory(settings, *, through_day: int | None = None, client=None) -> dict:
    """Trusted retry/backfill API; only persisted, completed ingestion days are used.

    Re-ingesting a day invokes this automatically. It never opens pack/answers or
    future source files. Call directly to retry pending extraction without a reset.
    """
    from workelate.ingestion.service import database_path

    if client is None:
        client = client_from_config(settings.llm)
    with connect_database(database_path(settings)) as conn:
        latest = conn.execute("SELECT MAX(day) FROM ingestion_history").fetchone()[0]
        limit = latest if through_day is None else min(through_day, latest or 0)
        records = list(conn.execute("""SELECT r.raw_json FROM records r
            JOIN ingestion_history h ON h.day=r.source_day
            WHERE r.source_day<=? AND NOT EXISTS (SELECT 1 FROM forgotten_records x WHERE x.record_id=r.id)
            ORDER BY r.occurred_at,r.id""", (limit or 0,)))
        for row in records:
            record = json.loads(row[0])
            if not _completed(conn, record["id"], "deterministic"):
                try:
                    with transaction(conn):
                        extract(conn, record)
                        _status(conn, record["id"], "deterministic", "completed", attempted=True)
                except Exception:
                    _status(conn, record["id"], "deterministic", "failed", "deterministic_extraction_error", attempted=True)
                    logger.warning("Deterministic extraction incomplete")
            if _completed(conn, record["id"], "deterministic"):
                try:
                    _semantic(conn, record, client, settings.llm)
                except Exception:
                    _status(conn, record["id"], "semantic", "failed", "semantic_extraction_error")
                    logger.warning("Semantic extraction incomplete")
        with transaction(conn):
            refresh_artifact_changes(conn)
        counts = {(row["kind"], row["status"]): row["count"] for row in conn.execute("""
            SELECT e.kind,e.status,COUNT(*) AS count FROM extraction_runs e JOIN records r ON r.id=e.record_id
            WHERE r.source_day<=? AND e.extractor_version=? GROUP BY e.kind,e.status""", (limit or 0, VERSION))}
        stats = {kind + "_" + state: counts.get((kind, state), 0)
                 for kind, states in (("deterministic", ("completed", "failed")),
                                      ("semantic", ("completed", "pending", "failed", "not_needed")))
                 for state in states}
        stats["status"] = "partial" if any(stats[key] for key in ("deterministic_failed", "semantic_pending", "semantic_failed")) else "complete"
        stats["facts_total"] = conn.execute("SELECT COUNT(*) FROM knowledge_facts").fetchone()[0]
        stats["commitments_total"] = conn.execute("SELECT COUNT(*) FROM commitments").fetchone()[0]
        stats["meetings_total"] = conn.execute("SELECT COUNT(DISTINCT org_id||':'||meeting_id) FROM meeting_memory").fetchone()[0]
        return stats
