"""Suppression markers deliberately contain no forgotten request/source text."""

FORGET_DDL = """
CREATE TABLE forget_rules (
 id TEXT PRIMARY KEY, person_id TEXT NOT NULL REFERENCES people(id), org_id TEXT NOT NULL REFERENCES organizations(id),
 scope TEXT NOT NULL, feedback_type TEXT, topic_hashes_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE forgotten_records (
 record_id TEXT PRIMARY KEY REFERENCES records(id), rule_id TEXT NOT NULL REFERENCES forget_rules(id)
);
CREATE TABLE forgotten_feedback (
 feedback_id INTEGER PRIMARY KEY REFERENCES feedback(id), rule_id TEXT NOT NULL REFERENCES forget_rules(id)
);
"""
FORGET_RESET_TABLES = ("forgotten_records", "forgotten_feedback", "forget_rules")
