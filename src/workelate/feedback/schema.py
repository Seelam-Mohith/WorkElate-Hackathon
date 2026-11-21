"""Additive Stage 5 feedback and brief persistence."""

LEARNING_DDL = """
CREATE TABLE feedback_processing (
 feedback_id INTEGER PRIMARY KEY REFERENCES feedback(id), person_id TEXT NOT NULL REFERENCES people(id),
 available_day INTEGER NOT NULL, available_at TEXT NOT NULL,
 status TEXT NOT NULL, error_code TEXT,
 model TEXT, input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
 cost_usd REAL, latency_ms INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE personal_fact_links (
 fact_id TEXT PRIMARY KEY REFERENCES knowledge_facts(id),
 feedback_id INTEGER NOT NULL UNIQUE REFERENCES feedback(id),
 person_id TEXT NOT NULL REFERENCES people(id), available_day INTEGER NOT NULL
);
CREATE TABLE preference_events (
 feedback_id INTEGER PRIMARY KEY REFERENCES feedback(id), person_id TEXT NOT NULL REFERENCES people(id),
 available_day INTEGER NOT NULL, available_at TEXT NOT NULL, preferences_json TEXT NOT NULL
);
CREATE TABLE verdict_events (
 feedback_id INTEGER PRIMARY KEY REFERENCES feedback(id), person_id TEXT NOT NULL REFERENCES people(id),
 available_day INTEGER NOT NULL, available_at TEXT NOT NULL,
 client_id TEXT REFERENCES clients(id), meeting_record_id TEXT NOT NULL REFERENCES records(id),
 topic TEXT NOT NULL, verdict TEXT NOT NULL
);
CREATE TABLE brief_cache (
 cache_key TEXT PRIMARY KEY, person_id TEXT NOT NULL REFERENCES people(id), meeting_id TEXT NOT NULL,
 local_date TEXT NOT NULL, through_day INTEGER NOT NULL, cutoff TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE brief_runs (
 id INTEGER PRIMARY KEY, person_id TEXT NOT NULL REFERENCES people(id), meeting_id TEXT NOT NULL,
 local_date TEXT NOT NULL, through_day INTEGER NOT NULL, cutoff TEXT NOT NULL,
 input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, cost_usd REAL,
 ms INTEGER NOT NULL, outcome TEXT NOT NULL, token_basis TEXT NOT NULL
);
"""

LEARNING_RESET_TABLES = ("brief_runs", "brief_cache", "verdict_events", "preference_events", "personal_fact_links", "feedback_processing")
