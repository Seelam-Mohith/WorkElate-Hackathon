"""Privacy-scoped RRF with explicit entity and structured evidence channels."""

from dataclasses import dataclass

from workelate.memory.repository import MemoryRepository
from workelate.retrieval.fts import keyword_search
from workelate.retrieval.vector_store import vector_search
from workelate.time_utils import parse_utc


def rrf(channels, *, depth=12):
    scores = {}
    for weight, ids in channels:
        for rank, record_id in enumerate(dict.fromkeys(ids), 1):
            scores[record_id] = scores.get(record_id, 0.0) + weight / (60 + rank)
    return sorted(scores, key=lambda record_id: (-scores[record_id], record_id))[:depth]


@dataclass
class Retrieved:
    records: list[dict]
    facts: list[dict]
    semantic_ids: set[str]


def retrieve(conn, question, person_id, time_query, resolution, classification, *, depth=12, query_vector=None, model_key=None, through_day=None, settings=None):
    allowed = {source["id"]: source for source in resolution.records if parse_utc(source["at"]) <= time_query.as_of}
    memory = MemoryRepository(conn, through_day=through_day)
    facts, structured, exact = [], [], []
    for entity in resolution.entities:
        if entity.kind in {"task", "client", "document", "sheet", "meeting"}:
            predicates = ("owner", "due_date") if classification.predicate == "owner_due" else (classification.predicate,) if classification.predicate else ()
            for predicate in predicates:
                current = memory.get_current_fact(entity.id, predicate, person_id, as_of=time_query.as_of,
                    subject_type=entity.kind, client_id=entity.client_id)
                if current:
                    facts.append(current)
                    structured.extend(current["source_ids"])
                    # Expand authorized historical evidence for comparisons and changes.
                    if classification.category == "changes":
                        for observation in memory.get_fact_history(entity.id, predicate, person_id, as_of=time_query.as_of):
                            structured.extend(observation["source_ids"])
        exact.extend(entity.sources)
    lexical = keyword_search(conn, question + " " + " ".join(entity.name for entity in resolution.entities), person_id, time_query.as_of, limit=depth, through_day=through_day)
    semantic = vector_search(conn, query_vector, model_key, person_id, time_query.as_of, limit=depth, through_day=through_day, settings=settings) if query_vector is not None else []
    ranked = rrf([(weight, [source for source in ids if source in allowed]) for weight, ids in
                  ((4, structured), (2, exact), (1, lexical), (1, semantic))], depth=depth)
    return Retrieved([allowed[record_id] for record_id in ranked if record_id in allowed], facts, set(semantic))
