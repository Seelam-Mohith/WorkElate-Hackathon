"""Real persistent Chroma contracts and offline Hugging Face provider tests."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from conftest import record
from test_search_infrastructure import MockEmbedder
from workelate.forgetting.service import forget_information
from workelate.ingestion.service import ingest_day
from workelate.retrieval import embeddings
from workelate.retrieval.embeddings import EmbeddingError, SearchConfig, embedding_client
from workelate.retrieval.indexing import index_records
from workelate.retrieval.vector_store import sync_vector_store, vector_search
from workelate.storage.database import connect_database

AT = "2026-11-02T23:00:00Z"


def test_huggingface_configuration_and_model_identity():
    config = SearchConfig.from_env({"WORKELATE_EMBEDDING_PROVIDER": "huggingface"})
    assert config.vector_backend == "chroma"
    assert config.model == "sentence-transformers/all-MiniLM-L6-v2"
    assert embedding_client(config).key != embedding_client(replace(config, revision="pinned")).key
    assert embedding_client(config).key == embedding_client(replace(config, device="cuda")).key
    assert SearchConfig.from_env({}).provider == "none"
    assert SearchConfig.from_env({"WORKELATE_VECTOR_BACKEND": "chroma"}).vector_backend == "chroma"
    with pytest.raises(ValueError):
        SearchConfig.from_env({"WORKELATE_VECTOR_BACKEND": "unknown"})


def test_huggingface_lazy_normalized_batches_and_safe_errors(monkeypatch, tmp_path):
    calls = []

    class Model:
        def encode(self, texts, **kwargs):
            calls.append((texts, kwargs))
            return SimpleNamespace(tolist=lambda: [[3.0, 4.0] for _ in texts])

    def load(*args):
        calls.append(args)
        return Model()

    monkeypatch.setattr(embeddings, "_local_model", load)
    config = SearchConfig.from_env({"WORKELATE_EMBEDDING_PROVIDER": "huggingface"})
    client = embedding_client(config, cache_dir=tmp_path)
    assert calls == []
    result = client.embed(["payment", "invoice"])
    assert result.vectors[0] == pytest.approx([0.6, 0.8])
    assert result.cost_usd == 0.0
    assert calls[0][-1] == str(tmp_path)
    assert calls[1][1]["normalize_embeddings"] is True

    def fail(*args):
        raise OSError("sensitive local path")

    monkeypatch.setattr(embeddings, "_local_model", fail)
    with pytest.raises(EmbeddingError, match="^huggingface_embedding_error$"):
        client.embed(["query"])


@pytest.fixture
def vector_pack(pack):
    pytest.importorskip("chromadb")
    pack.settings = replace(pack.settings, search=SearchConfig(vector_backend="chroma"))
    return pack


def search(pack, embedder, person="p2", at=AT, **kwargs):
    with connect_database(pack.database, read_only=True) as conn:
        return vector_search(conn, [1, 0], embedder.key, person, at, settings=pack.settings, **kwargs)


def test_chroma_backfills_cached_vectors_and_filters_before_ranking(vector_pack):
    pack = vector_pack
    pack.records(1, [record("billing", body="invoice"), record("design", body="colors"),
        record("private", body="invoice", visibility="private"),
        record("restricted", body="invoice", visibility="restricted", readers=["p2"]),
        record("foreign", body="invoice", org="org-b", author="q1"),
        record("future", body="invoice", at="2026-11-03T10:00:00Z")])
    sqlite_settings = replace(pack.settings, search=SearchConfig())
    ingest_day(1, sqlite_settings)
    embedder = MockEmbedder()
    index_records(sqlite_settings, embedder=embedder)
    embedder.calls.clear()
    result = index_records(pack.settings, embedder=embedder)
    assert result["vector_store"] == {"backend": "chroma", "status": "complete", "vectors": 6}
    assert embedder.calls == []
    assert search(pack, embedder, limit=1)[0] in {"billing", "restricted"}
    assert search(pack, embedder) == ["billing", "restricted"]
    assert search(pack, embedder, person="p1") == ["billing", "private"]
    assert search(pack, embedder, person="unknown") == []
    # A new adapter/client opens the same on-disk index on every search.
    assert search(pack, embedder) == ["billing", "restricted"]


def test_chroma_day_limit_reset_and_forget_cleanup(vector_pack):
    from workelate.retrieval.chroma_store import ChromaVectorStore

    pack = vector_pack
    pack.records(1, [record("old", body="invoice")])
    pack.records(2, [record("new", body="invoice")])
    embedder = MockEmbedder()
    ingest_day(1, pack.settings)
    index_records(pack.settings, embedder=embedder)
    ingest_day(2, pack.settings)
    index_records(pack.settings, embedder=embedder)
    assert search(pack, embedder, through_day=1) == ["old"]
    assert search(pack, embedder) == ["new", "old"]
    forget_information("p1", "forget all invoice information", pack.settings)
    assert search(pack, embedder) == []
    assert ChromaVectorStore(pack.settings.runtime_dir / "chroma").client.list_collections() == []
    index_records(pack.settings, embedder=embedder)
    assert search(pack, embedder) == []

    ingest_day(1, pack.settings)  # A fresh run clears the suppression rule.
    index_records(pack.settings, embedder=embedder)
    assert search(pack, embedder) == ["old"]
    collection = ChromaVectorStore(pack.settings.runtime_dir / "chroma")._collection(embedder.key)
    assert collection.count() == 1


def test_chroma_sync_failure_repair_and_search_fallback(vector_pack, monkeypatch):
    from workelate.retrieval.chroma_store import ChromaVectorStore

    pack = vector_pack
    pack.records(1, [record("billing", body="invoice")])
    ingest_day(1, pack.settings)
    embedder = MockEmbedder()
    original_sync = ChromaVectorStore.sync

    def fail(*args, **kwargs):
        raise RuntimeError("private provider contents")

    monkeypatch.setattr(ChromaVectorStore, "sync", fail)
    result = index_records(pack.settings, embedder=embedder)
    assert result["status"] == "partial"
    assert result["vector_store"]["error_code"] == "chroma_sync_error"
    embedder.calls.clear()
    monkeypatch.setattr(ChromaVectorStore, "sync", original_sync)
    assert index_records(pack.settings, embedder=embedder)["status"] == "complete"
    assert embedder.calls == []
    assert search(pack, embedder) == ["billing"]
    monkeypatch.setattr(ChromaVectorStore, "search", fail)
    assert search(pack, embedder) == []


def test_stale_chroma_vectors_are_denied_even_when_cleanup_fails(vector_pack, monkeypatch):
    from workelate.retrieval.chroma_store import ChromaVectorStore

    pack = vector_pack
    pack.records(1, [record("old", body="invoice")])
    ingest_day(1, pack.settings)
    embedder = MockEmbedder()
    index_records(pack.settings, embedder=embedder)
    original_sync = ChromaVectorStore.sync

    def offline(*args):
        raise OSError("offline")

    monkeypatch.setattr(ChromaVectorStore, "sync", offline)
    forget_information("p1", "forget invoice", pack.settings)
    assert search(pack, embedder) == []
    assert ChromaVectorStore(pack.settings.runtime_dir / "chroma")._collection(embedder.key).count() == 1
    monkeypatch.setattr(ChromaVectorStore, "sync", original_sync)
    with connect_database(pack.database) as conn:
        assert sync_vector_store(conn, pack.settings)["status"] == "complete"
    assert ChromaVectorStore(pack.settings.runtime_dir / "chroma").client.list_collections() == []


def test_chroma_separates_models_with_different_dimensions(vector_pack):
    pack = vector_pack
    pack.records(1, [record("billing", body="invoice")])
    ingest_day(1, pack.settings)
    first = MockEmbedder()
    index_records(pack.settings, embedder=first)

    class OtherModel(MockEmbedder):
        key = "other-model"

        def embed(self, texts):
            return embeddings.EmbeddingResult([[1, 0, 0] for _ in texts])

    second = OtherModel()
    assert index_records(pack.settings, embedder=second)["status"] == "complete"
    assert search(pack, first) == ["billing"]
    with connect_database(pack.database, read_only=True) as conn:
        assert vector_search(conn, [1, 0, 0], second.key, "p2", AT, settings=pack.settings) == ["billing"]


def test_answers_and_briefs_use_chroma(vector_pack, monkeypatch):
    from test_answers import SelectingClient, ask
    from test_briefs import brief, calendar
    from workelate.retrieval.chroma_store import ChromaVectorStore

    pack = vector_pack
    pack.records(1, [calendar(), record("billing", body="Invoice recipient is Person Two."),
        record("blocked", "task", status="blocked", change={"field": "status", "from": "todo", "to": "blocked"})])
    ingest_day(1, pack.settings)
    embedder = MockEmbedder()
    index_records(pack.settings, embedder=embedder)
    original_search = ChromaVectorStore.search
    calls = []

    def tracked(self, *args, **kwargs):
        calls.append(args[1])
        return original_search(self, *args, **kwargs)

    monkeypatch.setattr(ChromaVectorStore, "search", tracked)
    result = ask(pack, "Who accepts payments?", llm_client=SelectingClient(), embedder=embedder)
    assert result["answer"]["sources"] == ["billing"]
    assert calls == [embedder.key]
    result = brief(pack, embedder=embedder)
    assert result[0]["status"] == "brief"
    assert calls == [embedder.key, embedder.key]
