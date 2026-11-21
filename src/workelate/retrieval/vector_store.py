"""SQLite-backed cosine similarity; ACL and cutoff apply before reading vectors."""

import json

from workelate.privacy.access import ACCESS_SQL
from workelate.retrieval.embeddings import unit_vector
from workelate.time_utils import normalized_utc


def vector_search(conn, vector, model_key, person_id, cutoff, *, limit=12, through_day=None):
    query = unit_vector(vector)
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
