"""Suppress owned evidence, derived views and indexes atomically; never grant access."""

import hashlib
import json
import re

from workelate.memory.store import dump, identity, now
from workelate.storage.database import connect_database, transaction
from workelate.storage.repository import Repository

STOP = set("forget forgotten remove delete erase suppress stop remember remembering what i ive i've have had said told you my me mine about the a an all everything anything information info stored memory memories knowledge fact facts statement statements please preference preferences correction corrections verdict verdicts brief briefs meeting meetings of for on to in and that this from earlier personal regarding".split())


def _topic(text):
    words = set(re.findall(r"[\w-]+", text.casefold())) - STOP
    return {word[:-1] if len(word) > 3 and word.endswith("s") else word for word in words} - STOP


def _hashes(text):
    return {hashlib.sha256(word.encode()).hexdigest() for word in _topic(text)}


def _interpret(words):
    lower = words.casefold()
    kind = "preference" if re.search(r"\bpreferences?\b", lower) else "correction" if re.search(r"\bcorrections?\b", lower) else "verdict" if re.search(r"\bverdicts?\b", lower) else None
    topic = _hashes(words)
    # Empty/generic requests must not accidentally suppress an entire workplace.
    broad = bool(re.search(r"\b(all|everything)\b", lower))
    if not topic and not kind and not broad:
        return None
    return ("feedback" if kind else "all", kind, sorted(topic))


def _matches(rule, text):
    required = set(json.loads(rule["topic_hashes_json"]))
    return not required or required <= _hashes(text)


def apply_rules(conn):
    """Called inside the ingest/forget transaction before extraction or indexing."""
    affected_orgs, affected_people = set(), set()
    for row in conn.execute("SELECT * FROM forget_rules ORDER BY created_at,id").fetchall():
        rule = dict(row)
        person = Repository(conn).get_person(rule["person_id"])
        if person is None or person["org_id"] != rule["org_id"]:
            continue
        if rule["scope"] == "all":
            for record in Repository(conn).get_accessible_records(person["id"]):
                if record["author"] != person["id"]:
                    continue
                text = " ".join(str(record.get(key, "")) for key in ("id", "body", "title", "subject", "name", "task_id", "doc_id", "event_id"))
                if _matches(rule, text):
                    conn.execute("INSERT OR IGNORE INTO forgotten_records VALUES (?,?)", (record["id"], rule["id"]))
                    affected_orgs.add(person["org_id"])
        feedback = conn.execute("""SELECT f.* FROM feedback f WHERE f.person_id=? AND f.feedback_type<>'forget'
            AND NOT EXISTS (SELECT 1 FROM forgotten_feedback x WHERE x.feedback_id=f.id)""", (person["id"],)).fetchall()
        for source in feedback:
            if rule["feedback_type"] and source["feedback_type"] != rule["feedback_type"]:
                continue
            if not _matches(rule, source["words"] + " " + (source["topic"] or "")):
                continue
            feedback_id = source["id"]
            conn.execute("INSERT OR IGNORE INTO forgotten_feedback VALUES (?,?)", (feedback_id, rule["id"]))
            facts = [r[0] for r in conn.execute("SELECT fact_id FROM personal_fact_links WHERE feedback_id=?", (feedback_id,))]
            conn.execute("DELETE FROM personal_fact_links WHERE feedback_id=?", (feedback_id,))
            for fact_id in facts:
                conn.execute("DELETE FROM fact_history WHERE fact_id=?", (fact_id,))
                conn.execute("DELETE FROM fact_sources WHERE fact_id=?", (fact_id,))
                conn.execute("DELETE FROM knowledge_facts WHERE id=?", (fact_id,))
            conn.execute("DELETE FROM preference_events WHERE feedback_id=?", (feedback_id,))
            conn.execute("DELETE FROM verdict_events WHERE feedback_id=?", (feedback_id,))
            conn.execute("UPDATE feedback_processing SET status='forgotten',error_code=NULL WHERE feedback_id=?", (feedback_id,))
            affected_people.add(person["id"])
    # Remove the materialized search representations as well as filtering reads.
    ids = [r[0] for r in conn.execute("SELECT record_id FROM forgotten_records")]
    for record_id in ids:
        documents = conn.execute("SELECT scope_key,content_hash FROM search_documents WHERE record_id=?", (record_id,)).fetchall()
        for document in documents:
            conn.execute("DELETE FROM embedding_cache WHERE scope_key=? AND content_hash=?", tuple(document))
        for table in ("record_fts", "record_vectors", "search_documents", "indexing_status"):
            conn.execute(f"DELETE FROM {table} WHERE record_id=?", (record_id,))
    for org_id in affected_orgs:
        conn.execute("DELETE FROM brief_cache WHERE person_id IN (SELECT id FROM people WHERE org_id=?)", (org_id,))
    for person_id in affected_people:
        conn.execute("DELETE FROM brief_cache WHERE person_id=?", (person_id,))


def add_request(conn, person_id, words):
    person = Repository(conn).get_person(person_id)
    parsed = _interpret(words)
    if person is None or parsed is None:
        return
    scope, kind, topics = parsed
    rule_id = identity("forget-v1", person_id, person["org_id"], scope, kind, topics)
    conn.execute("INSERT OR IGNORE INTO forget_rules VALUES (?,?,?,?,?,?,?)",
        (rule_id, person_id, person["org_id"], scope, kind, dump(topics), now()))
    apply_rules(conn)


def process_forget_feedback(conn, through_day):
    """Apply newly arrived forget lines before any source is sent to a provider."""
    from workelate.feedback.service import available_at

    for row in conn.execute("""SELECT f.* FROM feedback f LEFT JOIN feedback_processing p ON p.feedback_id=f.id
        WHERE f.feedback_type='forget' AND f.source_day<? AND (p.status IS NULL OR p.status<>'applied')
        ORDER BY f.source_day,f.source_line""", (through_day,)).fetchall():
        person = Repository(conn).get_person(row["person_id"])
        add_request(conn, person["id"], row["words"])
        conn.execute("""INSERT OR REPLACE INTO feedback_processing
            (feedback_id,person_id,available_day,available_at,status,cost_usd)
            VALUES (?,?,?,?,'applied',0)""", (row["id"], person["id"], row["source_day"] + 1,
            available_at(row["source_day"], person["timezone"])))


def forget_information(person_id, words, settings):
    from workelate.ingestion.service import database_path

    path = database_path(settings)
    if path.exists():
        with connect_database(path) as conn, transaction(conn):
            add_request(conn, person_id, words)
        # External indexes are derived state: synchronize only after SQLite commits.
        with connect_database(path) as conn:
            from workelate.retrieval.vector_store import sync_vector_store

            sync_vector_store(conn, settings)
    return {"ok": True}
