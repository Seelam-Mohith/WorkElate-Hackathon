"""Additive, versioned migrations; existing source records are never rebuilt."""

import sqlite3
from workelate.retrieval.schema import SEARCH_DDL
from workelate.feedback.schema import LEARNING_DDL

MEMORY_DDL = """
CREATE TABLE knowledge_facts (
 id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(id),
 subject_type TEXT NOT NULL, subject_id TEXT NOT NULL, predicate TEXT NOT NULL,
 value_json TEXT NOT NULL, client_id TEXT REFERENCES clients(id),
 effective_at TEXT NOT NULL, observed_at TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'observed', confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
 extraction_method TEXT NOT NULL, authority TEXT NOT NULL,
 replaces_value_json TEXT, author_id TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX facts_lookup ON knowledge_facts(org_id,subject_id,predicate,effective_at);
CREATE TABLE fact_sources (
 fact_id TEXT NOT NULL REFERENCES knowledge_facts(id) ON DELETE CASCADE,
 record_id TEXT NOT NULL REFERENCES records(id), PRIMARY KEY(fact_id,record_id)
);
CREATE TABLE fact_history (
 id TEXT PRIMARY KEY, fact_id TEXT NOT NULL REFERENCES knowledge_facts(id) ON DELETE CASCADE,
 previous_value_json TEXT NOT NULL, new_value_json TEXT NOT NULL,
 changed_at TEXT NOT NULL, change_source_id TEXT NOT NULL REFERENCES records(id)
);
CREATE TABLE commitments (
 id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(id),
 client_id TEXT REFERENCES clients(id), kind TEXT NOT NULL CHECK(kind IN ('task','promise')),
 entity_id TEXT NOT NULL
);
CREATE TABLE commitment_observations (
 id TEXT PRIMARY KEY, commitment_id TEXT NOT NULL REFERENCES commitments(id) ON DELETE CASCADE,
 owner_id TEXT NOT NULL, description TEXT NOT NULL, deliverable_key TEXT NOT NULL,
 due_date TEXT, status TEXT NOT NULL CHECK(status IN ('open','in_progress','blocked','completed','cancelled','unknown')),
 effective_at TEXT NOT NULL, observed_at TEXT NOT NULL, authority TEXT NOT NULL,
 completion_record_id TEXT REFERENCES records(id)
);
CREATE INDEX commitments_lookup ON commitments(org_id,client_id);
CREATE TABLE commitment_sources (
 observation_id TEXT NOT NULL REFERENCES commitment_observations(id) ON DELETE CASCADE,
 record_id TEXT NOT NULL REFERENCES records(id), PRIMARY KEY(observation_id,record_id)
);
CREATE TABLE delivery_evidence (
 id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(id),
 owner_id TEXT NOT NULL, client_id TEXT REFERENCES clients(id),
 deliverable_key TEXT NOT NULL, effective_at TEXT NOT NULL,
 record_id TEXT NOT NULL REFERENCES records(id), evidence_quote TEXT NOT NULL
);
CREATE TABLE meeting_memory (
 id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL, org_id TEXT NOT NULL REFERENCES organizations(id),
 client_id TEXT REFERENCES clients(id), title TEXT NOT NULL, start_at TEXT NOT NULL,
 end_at TEXT, attendees_json TEXT NOT NULL, observed_at TEXT NOT NULL,
 record_id TEXT NOT NULL REFERENCES records(id)
);
CREATE INDEX meeting_lookup ON meeting_memory(org_id,meeting_id,observed_at);
CREATE TABLE meeting_sources (
 org_id TEXT NOT NULL REFERENCES organizations(id), meeting_id TEXT NOT NULL,
 record_id TEXT NOT NULL REFERENCES records(id), PRIMARY KEY(org_id,meeting_id,record_id)
);
CREATE TABLE artifact_versions (
 record_id TEXT PRIMARY KEY REFERENCES records(id), org_id TEXT NOT NULL REFERENCES organizations(id),
 kind TEXT NOT NULL CHECK(kind IN ('doc','sheet')), entity_id TEXT NOT NULL,
 version INTEGER NOT NULL, title TEXT NOT NULL, content_json TEXT NOT NULL,
 observed_at TEXT NOT NULL
);
CREATE INDEX artifact_lookup ON artifact_versions(org_id,kind,entity_id,version);
CREATE TABLE artifact_changes (
 id TEXT PRIMARY KEY, previous_record_id TEXT NOT NULL REFERENCES artifact_versions(record_id),
 record_id TEXT NOT NULL REFERENCES artifact_versions(record_id),
 field TEXT NOT NULL, previous_value_json TEXT NOT NULL, new_value_json TEXT NOT NULL
);
CREATE TABLE extraction_runs (
 id TEXT PRIMARY KEY, record_id TEXT NOT NULL REFERENCES records(id),
 extractor_version TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('deterministic','semantic')),
 status TEXT NOT NULL CHECK(status IN ('completed','pending','failed','not_needed')),
 attempts INTEGER NOT NULL DEFAULT 0, completed_at TEXT, error_message TEXT,
 UNIQUE(record_id,extractor_version,kind)
);
CREATE INDEX extraction_pending ON extraction_runs(kind,status);
CREATE TABLE llm_usage (
 id INTEGER PRIMARY KEY, extraction_run_id TEXT NOT NULL REFERENCES extraction_runs(id) ON DELETE CASCADE,
 model TEXT NOT NULL, input_tokens INTEGER, output_tokens INTEGER,
 cost_usd REAL, cost_basis TEXT NOT NULL, latency_ms INTEGER NOT NULL,
 outcome TEXT NOT NULL
);
"""

MEMORY_RESET_TABLES = (
    "llm_usage", "extraction_runs", "artifact_changes", "artifact_versions",
    "meeting_sources", "meeting_memory", "delivery_evidence", "commitment_sources",
    "commitment_observations", "commitments", "fact_history", "fact_sources", "knowledge_facts",
)


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)""")
        if conn.execute("SELECT 1 FROM schema_migrations WHERE version=1").fetchone() is None:
            for statement in MEMORY_DDL.split(";"):
                if statement.strip():
                    conn.execute(statement)
            conn.execute("INSERT INTO schema_migrations VALUES (1, 'structured_memory', strftime('%Y-%m-%dT%H:%M:%fZ','now'))")
        if conn.execute("SELECT 1 FROM schema_migrations WHERE version=2").fetchone() is None:
            # Association belongs to each sourced observation, never a global
            # identity row that might have originated in private evidence.
            conn.execute("ALTER TABLE commitment_observations ADD COLUMN client_id TEXT REFERENCES clients(id)")
            conn.execute("""UPDATE commitment_observations SET client_id=(
                SELECT r.client_id FROM commitment_sources s JOIN records r ON r.id=s.record_id
                WHERE s.observation_id=commitment_observations.id ORDER BY r.occurred_at,r.id LIMIT 1)""")
            conn.execute("INSERT INTO schema_migrations VALUES (2, 'commitment_observation_clients', strftime('%Y-%m-%dT%H:%M:%fZ','now'))")
        if conn.execute("SELECT 1 FROM schema_migrations WHERE version=3").fetchone() is None:
            for statement in SEARCH_DDL.split(";"):
                if statement.strip():
                    conn.execute(statement)
            conn.execute("INSERT INTO schema_migrations VALUES (3, 'hybrid_retrieval', strftime('%Y-%m-%dT%H:%M:%fZ','now'))")
        if conn.execute("SELECT 1 FROM schema_migrations WHERE version=4").fetchone() is None:
            for statement in LEARNING_DDL.split(";"):
                if statement.strip():
                    conn.execute(statement)
            conn.execute("INSERT INTO schema_migrations VALUES (4, 'briefs_and_personal_feedback', strftime('%Y-%m-%dT%H:%M:%fZ','now'))")
        if conn.execute("SELECT 1 FROM schema_migrations WHERE version=5").fetchone() is None:
            from workelate.forgetting.schema import FORGET_DDL
            for statement in FORGET_DDL.split(";"):
                if statement.strip():
                    conn.execute(statement)
            conn.execute("INSERT INTO schema_migrations VALUES (5, 'durable_forgetting', strftime('%Y-%m-%dT%H:%M:%fZ','now'))")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
