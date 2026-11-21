"""Stage 2 schema. Records are immutable historical source events."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY, name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS people (
    id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(id),
    name TEXT NOT NULL, role TEXT NOT NULL, team TEXT, timezone TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS people_org ON people(org_id);
CREATE TABLE IF NOT EXISTS clients (
    id TEXT PRIMARY KEY, name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS records (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations(id),
    record_type TEXT NOT NULL CHECK(record_type IN ('mail','chat','doc','sheet','task','event','note','notification')),
    occurred_at TEXT NOT NULL, original_at TEXT NOT NULL,
    author_id TEXT NOT NULL,
    client_id TEXT REFERENCES clients(id),
    visibility TEXT NOT NULL CHECK(visibility IN ('team','private','restricted')),
    body TEXT NOT NULL,
    source_day INTEGER NOT NULL CHECK(source_day > 0),
    source_line INTEGER NOT NULL CHECK(source_line > 0),
    raw_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS records_org_time ON records(org_id, occurred_at, id);
CREATE INDEX IF NOT EXISTS records_client_time ON records(org_id, client_id, occurred_at, id);
CREATE INDEX IF NOT EXISTS records_type ON records(org_id, record_type, occurred_at);
CREATE TABLE IF NOT EXISTS record_readers (
    record_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    person_id TEXT NOT NULL REFERENCES people(id),
    PRIMARY KEY(record_id, person_id)
);
CREATE INDEX IF NOT EXISTS readers_person ON record_readers(person_id, record_id);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY,
    source_day INTEGER NOT NULL CHECK(source_day > 0),
    source_line INTEGER NOT NULL CHECK(source_line > 0),
    fingerprint TEXT NOT NULL,
    person_id TEXT NOT NULL REFERENCES people(id),
    feedback_type TEXT NOT NULL CHECK(feedback_type IN ('preference','correction','verdict','forget')),
    words TEXT NOT NULL, meeting_id TEXT, topic TEXT, verdict TEXT,
    raw_json TEXT NOT NULL,
    UNIQUE(source_day, source_line)
);
CREATE INDEX IF NOT EXISTS feedback_person ON feedback(person_id, source_day);
CREATE TABLE IF NOT EXISTS ingestion_history (
    day INTEGER PRIMARY KEY CHECK(day > 0),
    status TEXT NOT NULL CHECK(status = 'completed'),
    records_processed INTEGER NOT NULL,
    feedback_processed INTEGER NOT NULL,
    completed_at TEXT NOT NULL
);
"""

RESET_TABLES = ("record_readers", "records", "feedback", "ingestion_history", "people", "clients", "organizations")
