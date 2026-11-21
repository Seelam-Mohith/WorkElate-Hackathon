"""Deterministic extraction from explicit source fields only; no language guesses."""

import json

from workelate.memory.store import TASK_STATES, commitment, dump, fact, identity
from workelate.time_utils import normalized_utc, parse_utc


def artifact_delta(previous: dict | str, current: dict | str, kind: str) -> list[tuple]:
    if kind == "doc":
        return [] if previous == current else [("content", previous, current)]
    return [(cell, previous.get(cell), current.get(cell))
            for cell in sorted(set(previous) | set(current)) if previous.get(cell) != current.get(cell)]


def extract(conn, record: dict) -> None:
    kind = record["type"]
    if kind == "task":
        change = record["change"]
        if change["field"] in {"owner", "due", "status"} and change["to"] != record[change["field"]]:
            raise ValueError("inconsistent_task_change")
        if conn.execute("SELECT 1 FROM people WHERE id=? AND org_id=?", (record["owner"], record["org"])).fetchone() is None:
            raise ValueError("unknown_task_owner")
        for field, predicate in (("title", "title"), ("owner", "owner"), ("due", "due_date"), ("status", "status")):
            authority = "change" if change["field"] == field else ("baseline" if change["field"] == "created" else "snapshot")
            fact(conn, record, "task", record["task_id"], predicate, record[field], authority=authority,
                 replaces=change.get("from") if change["field"] == field else None)
        commitment(conn, record, kind="task", entity_id=record["task_id"], owner_id=record["owner"],
            description=record["title"], deliverable=record["title"], due_date=record["due"],
            status=TASK_STATES[record["status"]], authority=change["field"])
    elif kind == "event":
        start = normalized_utc(record["start"])
        end = normalized_utc(record["end"]) if record.get("end") else None
        if end and parse_utc(end) < parse_utc(start):
            raise ValueError("invalid_event_end")
        meeting = record["event_id"]
        conn.execute("INSERT OR IGNORE INTO meeting_memory VALUES (?,?,?,?,?,?,?,?,?,?)",
            (identity("meeting", record["id"]), meeting, record["org"], record.get("client"), record["title"],
             start, end, dump(record["attendees"]), normalized_utc(record["at"]), record["id"]))
        for key, value in (("title", record["title"]), ("start_at", start), ("attendees", record["attendees"])):
            fact(conn, record, "meeting", meeting, key, value)
        if end:
            fact(conn, record, "meeting", meeting, "end_at", end)
    elif kind in {"doc", "sheet"}:
        entity = record["doc_id"] if kind == "doc" else record["sheet_id"]
        subject_type = "document" if kind == "doc" else "sheet"
        content = record["body"] if kind == "doc" else record["cells"]
        conn.execute("INSERT OR IGNORE INTO artifact_versions VALUES (?,?,?,?,?,?,?,?)",
            (record["id"], record["org"], kind, entity, record["version"],
             record.get("title", record.get("name")), dump(content), normalized_utc(record["at"])))
        fact(conn, record, subject_type, entity, "version", record["version"])
        fact(conn, record, subject_type, entity, "title", record.get("title", record.get("name")))
        if kind == "sheet":
            for cell, value in content.items():
                fact(conn, record, "sheet", entity, "cell:" + cell, value)
    elif kind in {"mail", "chat"}:
        for key, predicate in (("author", "author"), ("to", "recipients"), ("channel", "channel"),
                               ("subject", "subject"), ("at", "timestamp"), ("client", "client")):
            if key in record:
                fact(conn, record, "record", record["id"], predicate, record[key])
    if record.get("event_id") and kind in {"event", "note"}:
        conn.execute("INSERT OR IGNORE INTO meeting_sources VALUES (?,?,?)",
            (record["org"], record["event_id"], record["id"]))


def refresh_artifact_changes(conn) -> None:
    # Include both evidentiary endpoints. Retrieval recomputes consecutive versions
    # for each viewer rather than relying on a global (potentially private) delta.
    groups = {}
    for row in conn.execute("SELECT * FROM artifact_versions a WHERE NOT EXISTS (SELECT 1 FROM forgotten_records x WHERE x.record_id=a.record_id) ORDER BY org_id,kind,entity_id,version,observed_at,record_id"):
        groups.setdefault((row["org_id"], row["kind"], row["entity_id"]), []).append(dict(row))
    for (_, kind, _), versions in groups.items():
        for previous, current in zip(versions, versions[1:]):
            for field, old, new in artifact_delta(json.loads(previous["content_json"]), json.loads(current["content_json"]), kind):
                conn.execute("INSERT OR IGNORE INTO artifact_changes VALUES (?,?,?,?,?,?)",
                    (identity(previous["record_id"], current["record_id"], field), previous["record_id"],
                     current["record_id"], field, dump(old), dump(new)))
