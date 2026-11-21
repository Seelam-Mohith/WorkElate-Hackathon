"""Milestone A: persistent FTS/vectors, incremental indexing, and SQL privacy."""

import json

import pytest

from conftest import record
from workelate.ingestion.service import ingest_day
from workelate.retrieval.embeddings import EmbeddingResult
from workelate.retrieval.fts import keyword_search
from workelate.retrieval.indexing import index_records
from workelate.retrieval.vector_store import vector_search
from workelate.storage.database import connect_database


class MockEmbedder:
    model = "mock-semantic"
    key = "mock-v1"

    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(texts)
        return EmbeddingResult([[1.0, 0.0] if any(term in text.lower() for term in ("invoice", "billing", "payment")) else [0.0, 1.0] for text in texts], 7, 0.0)


def test_fts_exact_ids_names_titles_and_persistence(pack):
    pack.records(1, [record("task", "task", title="Invoice for Person One"), record("doc", "doc", title="Billing handbook")])
    result = ingest_day(1, pack.settings)
    assert result["indexing"]["fts_completed"] == 2
    with connect_database(pack.database, read_only=True) as conn:
        assert keyword_search(conn, "task-1", "p1", "2026-11-03T00:00:00Z")[0] == "task"
        assert keyword_search(conn, "Person One", "p1", "2026-11-03T00:00:00Z")[0] == "task"
        assert keyword_search(conn, "Billing handbook", "p1", "2026-11-03T00:00:00Z")[0] == "doc"
        assert conn.execute("SELECT COUNT(*) FROM record_fts").fetchone()[0] == 2


def test_fts_ranking_has_no_private_cross_org_or_future_influence(pack):
    pack.records(1, [record("allowed", body="rare invoice billing"), record("secret", body="invoice " * 100, visibility="private"),
                     record("other", body="invoice " * 200, org="org-b", author="q1"),
                     record("future", body="invoice " * 100, at="2026-11-03T10:00:00Z")])
    ingest_day(1, pack.settings)
    with connect_database(pack.database, read_only=True) as conn:
        assert keyword_search(conn, "invoice", "p2", "2026-11-02T23:00:00Z") == ["allowed"]
        assert keyword_search(conn, "invoice", "unknown", "2026-11-02T23:00:00Z") == []
        assert "secret" in keyword_search(conn, "invoice", "p1", "2026-11-02T23:00:00Z")


def test_vector_paraphrases_permissions_cutoff_and_persistence(pack):
    pack.records(1, [record("billing", body="invoice recipient"), record("other", body="design colors"),
                     record("secret", body="invoice", visibility="private"),
                     record("foreign", body="invoice", org="org-b", author="q1"),
                     record("future", body="invoice", at="2026-11-03T10:00:00Z")])
    ingest_day(1, pack.settings)
    embedder = MockEmbedder()
    assert index_records(pack.settings, embedder=embedder)["vectors_completed"] == 5
    with connect_database(pack.database, read_only=True) as conn:
        query = embedder.embed(["payment details"]).vectors[0]
        assert vector_search(conn, query, embedder.key, "p2", "2026-11-02T23:00:00Z") == ["billing"]
        assert vector_search(conn, query, embedder.key, "unknown", "2026-11-02T23:00:00Z") == []


def test_cache_reuse_incremental_and_org_separation(pack):
    pack.records(1, [record("a", body="invoice"), record("b", body="invoice"), record("foreign", org="org-b", author="q1", body="invoice")])
    ingest_day(1, pack.settings)
    embedder = MockEmbedder()
    index_records(pack.settings, embedder=embedder)
    assert len(embedder.calls) == 2  # Separate organization requests, deduplicated within each.
    assert all(len(batch) == 1 for batch in embedder.calls)
    index_records(pack.settings, embedder=embedder)
    assert len(embedder.calls) == 2
    pack.records(2, [record("later", body="invoice")])
    ingest_day(2, pack.settings)
    index_records(pack.settings, embedder=embedder)
    assert len(embedder.calls) == 2


def test_provider_failure_retains_keyword_index_and_can_retry(pack):
    class Offline(MockEmbedder):
        def embed(self, texts):
            raise TimeoutError("do not expose this text")

    ingest_day(1, pack.settings)
    result = index_records(pack.settings, embedder=Offline())
    assert result["fts_completed"] == result["vectors_failed"] == 1
    assert result["status"] == "partial"
    assert index_records(pack.settings, embedder=MockEmbedder())["vectors_completed"] == 1


def test_no_future_day_indexing_and_day_one_reset(pack):
    pack.records(1, [record("one", body="one")])
    pack.records(2, [record("two", body="two")])
    ingest_day(1, pack.settings)
    with connect_database(pack.database, read_only=True) as conn:
        assert [row[0] for row in conn.execute("SELECT record_id FROM search_documents")] == ["one"]
    ingest_day(2, pack.settings)
    ingest_day(1, pack.settings)
    with connect_database(pack.database, read_only=True) as conn:
        assert [row[0] for row in conn.execute("SELECT record_id FROM record_fts")] == ["one"]
