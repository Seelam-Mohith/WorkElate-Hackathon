"""Incremental local FTS and optional, cached embedding indexing."""

import json
import time
from collections import defaultdict

from workelate.memory.store import dump, identity
from workelate.retrieval.embeddings import EmbeddingError, embedding_client, unit_vector
from workelate.storage.database import connect_database, transaction


def searchable(record):
    title = str(record.get("title") or record.get("subject") or record.get("name") or "")
    metadata = {key: record[key] for key in ("task_id", "doc_id", "sheet_id", "event_id", "owner", "due", "status", "channel", "to", "attendees", "cells") if key in record}
    return title, record["body"] + "\n" + dump(metadata)


def index_records(settings, *, through_day=None, embedder=None):
    from workelate.ingestion.service import database_path

    embedder = embedder or embedding_client(settings.search)
    model_key = embedder.key if embedder else "none"
    with connect_database(database_path(settings)) as conn:
        limit = conn.execute("SELECT MAX(day) FROM ingestion_history").fetchone()[0] or 0
        limit = min(limit, through_day) if through_day is not None else limit
        batches = defaultdict(list)
        for row in conn.execute("SELECT raw_json FROM records r WHERE source_day<=? AND NOT EXISTS (SELECT 1 FROM forgotten_records x WHERE x.record_id=r.id) ORDER BY occurred_at,id", (limit,)).fetchall():
            record = json.loads(row[0])
            title, body = searchable(record)
            content_hash = identity(title, body)
            scope = identity(record["org"], record["visibility"],
                record["author"] if record["visibility"] == "private" else sorted(record.get("readers", [])) if record["visibility"] == "restricted" else "team")
            previous = conn.execute("SELECT * FROM indexing_status WHERE record_id=?", (record["id"],)).fetchone()
            if previous and previous["content_hash"] == content_hash and previous["fts_status"] == "completed" and previous["model_key"] == model_key and previous["vector_status"] in {"completed", "pending" if embedder is None else "completed"}:
                continue
            try:
                with transaction(conn):
                    conn.execute("DELETE FROM record_fts WHERE record_id=?", (record["id"],))
                    conn.execute("INSERT INTO record_fts VALUES (?,?,?)", (record["id"], title, body))
                    conn.execute("INSERT OR REPLACE INTO search_documents VALUES (?,?,?,?,?)", (record["id"], title, body, content_hash, scope))
                    conn.execute("INSERT OR REPLACE INTO indexing_status VALUES (?,?,'completed','pending',?,NULL)", (record["id"], content_hash, model_key))
                if embedder:
                    cached = conn.execute("SELECT vector_json FROM embedding_cache WHERE scope_key=? AND model_key=? AND content_hash=?", (scope, model_key, content_hash)).fetchone()
                    if cached:
                        with transaction(conn):
                            conn.execute("INSERT OR REPLACE INTO record_vectors VALUES (?,?,?,?)", (record["id"], model_key, content_hash, cached[0]))
                            conn.execute("UPDATE indexing_status SET vector_status='completed' WHERE record_id=?", (record["id"],))
                    else:
                        batches[(record["org"], scope)].append((record["id"], content_hash, title + "\n" + body))
            except Exception:
                conn.execute("INSERT OR REPLACE INTO indexing_status VALUES (?,?,'failed','pending',?,'fts_indexing_error')", (record["id"], content_hash, model_key))
        for (_, scope), entries in batches.items():
            for offset in range(0, len(entries), settings.search.batch_size):
                batch = entries[offset:offset + settings.search.batch_size]
                # Identical content within a batch requires one embedding only.
                unique = {item[1]: item[2] for item in batch}
                cached = {content_hash: conn.execute("SELECT vector_json FROM embedding_cache WHERE scope_key=? AND model_key=? AND content_hash=?", (scope, model_key, content_hash)).fetchone() for content_hash in unique}
                if all(cached.values()):
                    with transaction(conn):
                        for record_id, content_hash, _ in batch:
                            conn.execute("INSERT OR REPLACE INTO record_vectors VALUES (?,?,?,?)", (record_id, model_key, content_hash, cached[content_hash][0]))
                            conn.execute("UPDATE indexing_status SET vector_status='completed',error_code=NULL WHERE record_id=?", (record_id,))
                    continue
                started = time.monotonic()
                result, error = None, None
                try:
                    missing = {content_hash: text for content_hash, text in unique.items() if cached[content_hash] is None}
                    result = embedder.embed([text[:8000] for text in missing.values()])
                    vectors = [unit_vector(vector) for vector in result.vectors]
                    if len(vectors) != len(missing) or len({len(vector) for vector in vectors}) != 1:
                        raise EmbeddingError("invalid_embedding_batch")
                    dimensions = len(vectors[0])
                    previous = conn.execute("SELECT dimensions FROM embedding_models WHERE model_key=?", (model_key,)).fetchone()
                    if previous and previous[0] != dimensions:
                        raise EmbeddingError("embedding_dimension_mismatch")
                    by_hash = {content_hash: json.loads(row[0]) for content_hash, row in cached.items() if row is not None}
                    by_hash.update(zip(missing, vectors))
                    with transaction(conn):
                        conn.execute("INSERT OR IGNORE INTO embedding_models VALUES (?,?)", (model_key, dimensions))
                        for record_id, content_hash, _ in batch:
                            vector_json = dump(by_hash[content_hash])
                            conn.execute("INSERT OR REPLACE INTO embedding_cache VALUES (?,?,?,?)", (scope, model_key, content_hash, vector_json))
                            conn.execute("INSERT OR REPLACE INTO record_vectors VALUES (?,?,?,?)", (record_id, model_key, content_hash, vector_json))
                            conn.execute("UPDATE indexing_status SET vector_status='completed',error_code=NULL WHERE record_id=?", (record_id,))
                except Exception:
                    error = "embedding_indexing_error"
                    conn.executemany("UPDATE indexing_status SET vector_status='failed',error_code=? WHERE record_id=?", [(error, item[0]) for item in batch])
                conn.execute("INSERT INTO search_usage (phase,model,input_tokens,cost_usd,latency_ms,outcome) VALUES ('index',?,?,?,?,?)",
                    (embedder.model, result.input_tokens if result else None, result.cost_usd if result else None,
                     int((time.monotonic() - started) * 1000), error or "completed"))
        counts = dict(conn.execute("SELECT vector_status,COUNT(*) FROM indexing_status s JOIN records r ON r.id=s.record_id WHERE r.source_day<=? GROUP BY vector_status", (limit,)).fetchall())
        failures = conn.execute("SELECT COUNT(*) FROM indexing_status s JOIN records r ON r.id=s.record_id WHERE r.source_day<=? AND s.fts_status='failed'", (limit,)).fetchone()[0]
        return {"status": "partial" if failures or counts.get("failed", 0) or counts.get("pending", 0) else "complete",
                "fts_completed": conn.execute("SELECT COUNT(*) FROM indexing_status s JOIN records r ON r.id=s.record_id WHERE r.source_day<=? AND s.fts_status='completed'", (limit,)).fetchone()[0],
                "fts_failed": failures, "vectors_completed": counts.get("completed", 0), "vectors_pending": counts.get("pending", 0), "vectors_failed": counts.get("failed", 0)}
