"""Selectable vector index with SQLite as the authority for access and cutoffs."""

import json
import logging

from workelate.privacy.access import ACCESS_SQL
from workelate.retrieval.embeddings import unit_vector
from workelate.time_utils import normalized_utc


def sync_vector_store(conn, settings):
    if settings.search.vector_backend == "sqlite":
        return {"backend": "sqlite", "status": "complete"}
    try:
        from workelate.retrieval.chroma_store import ChromaVectorStore

        count = ChromaVectorStore(settings.runtime_dir / "chroma").sync(conn)
        return {"backend": "chroma", "status": "complete", "vectors": count}
    except Exception:
        logging.getLogger("workelate").warning("Chroma synchronization failed; retry indexing to repair the vector index.")
        return {"backend": "chroma", "status": "failed", "error_code": "chroma_sync_error"}


def vector_search(conn, vector, model_key, person_id, cutoff, *, limit=12, through_day=None, settings=None):
    query = unit_vector(vector)
    if settings is not None and settings.search.vector_backend == "chroma":
        eligible = conn.execute("""SELECT v.record_id,v.content_hash FROM record_vectors v
            JOIN records r ON r.id=v.record_id JOIN people p ON p.id=?
            JOIN search_documents d ON d.record_id=v.record_id AND d.content_hash=v.content_hash
            WHERE """ + ACCESS_SQL + " AND r.occurred_at<=? AND v.model_key=? AND r.source_day<=?",
            (person_id, normalized_utc(cutoff), model_key, through_day if through_day is not None else 2**31)).fetchall()
        if not eligible:
            return []
        try:
            from workelate.retrieval.chroma_store import ChromaVectorStore

            return ChromaVectorStore(settings.runtime_dir / "chroma").search(query, model_key, eligible, limit=limit)
        except Exception:
            logging.getLogger("workelate").warning("Chroma search unavailable; using keyword and structured evidence.")
            return []
    scored = []
    for row in conn.execute("""SELECT v.record_id,v.vector_json FROM record_vectors v
        JOIN records r ON r.id=v.record_id JOIN people p ON p.id=? WHERE """ + ACCESS_SQL + " AND r.occurred_at<=? AND v.model_key=? AND r.source_day<=?",
        (person_id, normalized_utc(cutoff), model_key, through_day if through_day is not None else 2**31)):
        values = json.loads(row[1])
        if len(values) == len(query):
            score = sum(a * b for a, b in zip(query, values))
            if score > 0:
                scored.append((score, row[0]))
    return [record_id for _, record_id in sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]]
