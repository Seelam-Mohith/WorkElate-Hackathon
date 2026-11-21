"""Personal settings and feedback provenance, bounded by time and ingested day."""

import json

from workelate.storage.repository import Repository
from workelate.time_utils import normalized_utc, parse_utc


def preferences_for(conn, person_id, cutoff, *, through_day=None):
    result = {"max_changes": 8, "blockers_first": False, "skip_newsletters": False, "my_tasks_first": False}
    for row in conn.execute("""SELECT * FROM preference_events WHERE person_id=? AND available_at<=?
        AND available_day<=? ORDER BY available_at,feedback_id""", (person_id, normalized_utc(cutoff), through_day if through_day is not None else 2**31)):
        result.update(json.loads(row["preferences_json"]))
    return result


def verdict_score(conn, person_id, client_id, text, cutoff, *, through_day=None):
    from workelate.retrieval.entity_resolution import tokens

    score = 0
    repository = Repository(conn, through_day=through_day)
    for row in conn.execute("""SELECT * FROM verdict_events WHERE person_id=? AND client_id IS ?
        AND available_at<=? AND available_day<=? ORDER BY available_at,feedback_id""",
        (person_id, client_id, normalized_utc(cutoff), through_day if through_day is not None else 2**31)):
        anchor = repository.get_record(row["meeting_record_id"], person_id)
        if anchor is None or parse_utc(anchor["at"]) > parse_utc(cutoff):
            continue
        topic = tokens(row["topic"])
        if topic and len(topic & tokens(text)) >= min(2, len(topic)):
            score += 1 if row["verdict"] == "kept" else -1
    return max(-3, min(3, score))


def feedback_fact_valid(conn, fact_id, person_id, cutoff, *, through_day=None):
    return conn.execute("""SELECT 1 FROM personal_fact_links l
        JOIN feedback_processing p ON p.feedback_id=l.feedback_id
        JOIN feedback f ON f.id=l.feedback_id
        JOIN people u ON u.id=l.person_id
        JOIN knowledge_facts k ON k.id=l.fact_id
        WHERE l.fact_id=? AND l.person_id=? AND p.status='applied'
        AND p.available_at<=? AND l.available_day<=? AND u.org_id=k.org_id AND f.person_id=l.person_id""",
        (fact_id, person_id, normalized_utc(cutoff), through_day if through_day is not None else 2**31)).fetchone() is not None
