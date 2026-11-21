"""Trusted writes of immutable observations. User reads belong in repository.py."""

import hashlib
import json
from datetime import datetime, timezone

from workelate.time_utils import normalized_utc


def dump(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def identity(*parts) -> str:
    return hashlib.sha256(dump(parts).encode()).hexdigest()


def now() -> str:
    return normalized_utc(datetime.now(timezone.utc))


def fact(conn, record: dict, subject_type: str, subject_id: str, predicate: str, value,
         *, authority="snapshot", method="deterministic", confidence=1.0,
         effective_at=None, replaces=None, sources=None) -> str:
    source_ids = sorted(set(sources or [record["id"]]))
    effective = normalized_utc(effective_at or record["at"])
    fact_id = identity("fact-v1", record["org"], subject_type, subject_id, predicate,
                       value, source_ids, effective, method)
    conn.execute("""INSERT OR IGNORE INTO knowledge_facts
        (id,org_id,subject_type,subject_id,predicate,value_json,client_id,effective_at,
         observed_at,status,confidence,extraction_method,authority,replaces_value_json,author_id,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,'observed',?,?,?,?,?,?)""",
        (fact_id, record["org"], subject_type, subject_id, predicate, dump(value), record.get("client"),
         effective, normalized_utc(record["at"]), confidence, method, authority,
         dump(replaces) if replaces is not None else None, record["author"], now()))
    conn.executemany("INSERT OR IGNORE INTO fact_sources VALUES (?,?)", [(fact_id, source) for source in source_ids])
    if replaces is not None:
        conn.execute("INSERT OR IGNORE INTO fact_history VALUES (?,?,?,?,?,?)",
            (identity("change", fact_id), fact_id, dump(replaces), dump(value), effective, record["id"]))
    return fact_id


TASK_STATES = {"todo": "open", "in_progress": "in_progress", "blocked": "blocked", "done": "completed", "cancelled": "cancelled"}


def deliverable_key(text: str) -> str:
    # Exact normalization only, not semantic entity resolution or fuzzy matching.
    return " ".join(text.casefold().split())


def commitment(conn, record: dict, *, kind: str, entity_id: str, owner_id: str,
               description: str, deliverable: str, due_date: str | None,
               status: str, authority: str) -> str:
    commitment_id = identity("commitment-v1", record["org"], kind, entity_id)
    conn.execute("INSERT OR IGNORE INTO commitments VALUES (?,?,?,?,?)",
        (commitment_id, record["org"], record.get("client"), kind, entity_id))
    observation = identity(commitment_id, record["id"])
    conn.execute("INSERT OR IGNORE INTO commitment_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (observation, commitment_id, owner_id, description, deliverable_key(deliverable), due_date,
         status, normalized_utc(record["at"]), normalized_utc(record["at"]), authority,
         record["id"] if status == "completed" else None, record.get("client")))
    conn.execute("INSERT OR IGNORE INTO commitment_sources VALUES (?,?)", (observation, record["id"]))
    return commitment_id
