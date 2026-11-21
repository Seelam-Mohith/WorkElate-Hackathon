"""Optional embedding provider. No fabricated/hash embeddings or model downloads."""

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

from workelate.reasoning.llm_client import _NoRedirect


class EmbeddingError(Exception):
    pass


@dataclass(frozen=True)
class SearchConfig:
    provider: str = "none"
    model: str = ""
    url: str = "https://api.openai.com/v1/embeddings"
    api_key: str = field(default="", repr=False)
    timeout: float = 15
    depth: int = 12
    batch_size: int = 16
    input_rate: float | None = None

    @classmethod
    def from_env(cls, env):
        provider = env.get("WORKELATE_EMBEDDING_PROVIDER", "none")
        timeout = float(env.get("WORKELATE_EMBEDDING_TIMEOUT", "15"))
        depth = int(env.get("WORKELATE_RETRIEVAL_DEPTH", "12"))
        batch = int(env.get("WORKELATE_EMBEDDING_BATCH_SIZE", "16"))
        rate = float(env["WORKELATE_EMBEDDING_INPUT_USD_PER_MILLION"]) if env.get("WORKELATE_EMBEDDING_INPUT_USD_PER_MILLION") else None
        if provider not in {"none", "openai-compatible"} or not math.isfinite(timeout) or not 0 < timeout <= 60 or not 1 <= depth <= 50 or not 1 <= batch <= 64:
            raise ValueError("Invalid search configuration")
        if rate is not None and (not math.isfinite(rate) or rate < 0):
            raise ValueError("Invalid embedding rate")
        return cls(provider, env.get("WORKELATE_EMBEDDING_MODEL", ""), env.get("WORKELATE_EMBEDDING_URL", cls.url),
                   env.get("WORKELATE_EMBEDDING_API_KEY", ""), timeout, depth, batch, rate)


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    input_tokens: int | None = None
    cost_usd: float | None = None


class Embedder(Protocol):
    model: str
    key: str

    def embed(self, texts: list[str]) -> EmbeddingResult: ...


def unit_vector(values) -> list[float]:
    if not isinstance(values, list) or not values or any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        raise EmbeddingError("invalid_vector")
    norm = math.hypot(*values)
    if not math.isfinite(norm) or norm == 0:
        raise EmbeddingError("invalid_vector")
    return [value / norm for value in values]


class HTTPEmbedder:
    def __init__(self, config: SearchConfig):
        from workelate.memory.store import identity
        self.config, self.model = config, config.model
        self.key = identity(config.provider, config.url, config.model)

    def embed(self, texts: list[str]) -> EmbeddingResult:
        config = self.config
        try:
            url = urllib.parse.urlsplit(config.url)
            if not self.model or not url.hostname or url.username or url.password or url.fragment:
                raise EmbeddingError("embedding_not_configured")
            if url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}):
                raise EmbeddingError("insecure_embedding_url")
            if url.hostname == "api.openai.com" and not config.api_key:
                raise EmbeddingError("embedding_not_configured")
            headers = {"Content-Type": "application/json"}
            if config.api_key:
                headers["Authorization"] = "Bearer " + config.api_key
            request = urllib.request.Request(config.url, headers=headers, data=json.dumps({"model": self.model, "input": texts}).encode())
            with urllib.request.build_opener(_NoRedirect()).open(request, timeout=config.timeout) as response:
                payload = response.read(8_000_001)
            if len(payload) > 8_000_000:
                raise EmbeddingError("embedding_response_too_large")
            data = json.loads(payload)
            entries = sorted(data["data"], key=lambda item: item["index"])
            if [entry["index"] for entry in entries] != list(range(len(texts))):
                raise EmbeddingError("invalid_embedding_response")
            vectors = [unit_vector(entry["embedding"]) for entry in entries]
            if len({len(vector) for vector in vectors}) > 1:
                raise EmbeddingError("embedding_dimension_mismatch")
            tokens = (data.get("usage") or {}).get("prompt_tokens")
            if tokens is not None and (type(tokens) is not int or tokens < 0):
                raise EmbeddingError("invalid_embedding_usage")
            cost = tokens * config.input_rate / 1_000_000 if tokens is not None and config.input_rate is not None else None
            return EmbeddingResult(vectors, tokens, cost)
        except EmbeddingError:
            raise
        except Exception:
            raise EmbeddingError("embedding_provider_error") from None


def embedding_client(config: SearchConfig) -> Embedder | None:
    return HTTPEmbedder(config) if config.provider != "none" else None
