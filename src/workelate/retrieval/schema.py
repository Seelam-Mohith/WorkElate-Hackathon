"""Stage 4 additive search and ask audit schema."""

SEARCH_DDL = """
CREATE TABLE search_documents (
 record_id TEXT PRIMARY KEY REFERENCES records(id), title TEXT NOT NULL, body TEXT NOT NULL,
 content_hash TEXT NOT NULL, scope_key TEXT NOT NULL
);
CREATE VIRTUAL TABLE record_fts USING fts5(record_id UNINDEXED,title,body,tokenize='porter unicode61');
CREATE TABLE embedding_models (model_key TEXT PRIMARY KEY, dimensions INTEGER NOT NULL);
CREATE TABLE record_vectors (
 record_id TEXT NOT NULL REFERENCES records(id), model_key TEXT NOT NULL REFERENCES embedding_models(model_key),
 content_hash TEXT NOT NULL, vector_json TEXT NOT NULL, PRIMARY KEY(record_id,model_key)
);
CREATE TABLE embedding_cache (
 scope_key TEXT NOT NULL, model_key TEXT NOT NULL REFERENCES embedding_models(model_key),
 content_hash TEXT NOT NULL, vector_json TEXT NOT NULL, PRIMARY KEY(scope_key,model_key,content_hash)
);
CREATE TABLE indexing_status (
 record_id TEXT PRIMARY KEY REFERENCES records(id), content_hash TEXT NOT NULL,
 fts_status TEXT NOT NULL, vector_status TEXT NOT NULL, model_key TEXT, error_code TEXT
);
CREATE TABLE search_usage (
 id INTEGER PRIMARY KEY, phase TEXT NOT NULL, model TEXT NOT NULL,
 input_tokens INTEGER, cost_usd REAL, latency_ms INTEGER NOT NULL, outcome TEXT NOT NULL
);
CREATE TABLE ask_runs (
 id INTEGER PRIMARY KEY, person_id TEXT NOT NULL REFERENCES people(id), cutoff TEXT NOT NULL,
 input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, cost_usd REAL,
 token_basis TEXT NOT NULL, ms INTEGER NOT NULL, outcome TEXT NOT NULL
);
"""

SEARCH_RESET_TABLES = ("ask_runs", "search_usage", "record_fts", "indexing_status", "record_vectors",
                       "embedding_cache", "embedding_models", "search_documents")
