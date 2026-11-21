"""Persistent Chroma index derived from SQLite; no implicit embedding function."""

import json
from collections import defaultdict

from workelate.memory.store import identity

PREFIX = "workelate-"
BATCH_SIZE = 256


def vector_id(record_id, model_key, content_hash):
    return identity(record_id, model_key, content_hash)


class ChromaVectorStore:
    def __init__(self, path):
        import chromadb
        from chromadb.config import Settings

        self.client = chromadb.PersistentClient(path=str(path), settings=Settings(anonymized_telemetry=False))

    def _collection(self, model_key):
        return self.client.get_or_create_collection(
            name=PREFIX + identity(model_key), embedding_function=None,
            configuration={"hnsw": {"space": "cosine"}},
        )

    def sync(self, conn):
        """Repair/backfill from cached vectors and remove reset/forgotten entries.

        SQLite commits first. Repeating this operation repairs interrupted writes
        without calling the embedding provider again.
        """
        models = defaultdict(dict)
        for row in conn.execute("""SELECT v.* FROM record_vectors v JOIN records r ON r.id=v.record_id
            JOIN search_documents d ON d.record_id=v.record_id AND d.content_hash=v.content_hash
            WHERE NOT EXISTS (SELECT 1 FROM forgotten_records f WHERE f.record_id=v.record_id)"""):
            models[row["model_key"]][vector_id(row["record_id"], row["model_key"], row["content_hash"])] = row
        expected = {PREFIX + identity(key) for key in models}
        for collection in self.client.list_collections():
            name = collection if isinstance(collection, str) else collection.name
            if name.startswith(PREFIX) and name not in expected:
                self.client.delete_collection(name)
        indexed = 0
        for model_key, rows in models.items():
            collection = self._collection(model_key)
            existing = set(collection.get(include=[])["ids"])
            stale = sorted(existing - rows.keys())
            for offset in range(0, len(stale), BATCH_SIZE):
                collection.delete(ids=stale[offset:offset + BATCH_SIZE])
            missing = sorted(rows.keys() - existing)
            for offset in range(0, len(missing), BATCH_SIZE):
                ids = missing[offset:offset + BATCH_SIZE]
                collection.upsert(ids=ids,
                    embeddings=[json.loads(rows[key]["vector_json"]) for key in ids],
                    metadatas=[{"record_id": rows[key]["record_id"]} for key in ids])
            indexed += len(rows)
        return indexed

    def search(self, query, model_key, eligible, *, limit):
        """Filter by exact SQL-authorized vector IDs before nearest-neighbor search."""
        allowed = {vector_id(row["record_id"], model_key, row["content_hash"]): row["record_id"] for row in eligible}
        if not allowed or limit <= 0:
            return []
        collection = self._collection(model_key)
        scored = []
        # Bound filter size. Top-k from each disjoint group suffices for global top-k.
        ids = sorted(allowed)
        for offset in range(0, len(ids), BATCH_SIZE):
            batch = ids[offset:offset + BATCH_SIZE]
            result = collection.query(query_embeddings=[query], ids=batch,
                                      n_results=min(limit, len(batch)), include=["distances"])
            for key, distance in zip(result["ids"][0], result["distances"][0]):
                if key in allowed and distance < 1.0:
                    scored.append((distance, allowed[key]))
        return [record_id for _, record_id in sorted(scored, key=lambda item: (item[0], item[1]))[:limit]]
