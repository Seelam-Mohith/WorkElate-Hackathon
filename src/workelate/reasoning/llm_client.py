"""Small provider boundary plus an optional Chat Completions-compatible HTTP adapter.

No SDK, paid calls, or network access is required by the default configuration.
HTTP errors expose safe codes only. Response bodies and credentials are never logged.
"""

import json
import math
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Mapping, Protocol


class LLMError(Exception):
    def __init__(self, code: str, *, retryable: bool = False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "none"
    model: str = ""
    url: str = "https://api.openai.com/v1/chat/completions"
    api_key: str = field(default="", repr=False)
    timeout: float = 30.0
    attempts: int = 2
    max_source_chars: int = 16000
    input_usd_per_million: float | None = None
    output_usd_per_million: float | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, prefix: str = "WORKELATE_LLM_"):
        env = os.environ if env is None else env
        provider = env.get(prefix + "PROVIDER", "none")
        if provider not in {"none", "openai-compatible"}:
            raise ValueError("Invalid LLM provider")
        timeout = float(env.get(prefix + "TIMEOUT", "30"))
        attempts = int(env.get(prefix + "ATTEMPTS", "2"))
        max_chars = int(env.get(prefix + "MAX_SOURCE_CHARS", "16000"))
        if not math.isfinite(timeout) or not 0 < timeout <= 120 or not 1 <= attempts <= 3 or max_chars <= 0:
            raise ValueError("Invalid LLM limits")
        rates = []
        for name in ("INPUT_USD_PER_MILLION", "OUTPUT_USD_PER_MILLION"):
            raw = env.get(prefix + name)
            rate = float(raw) if raw else None
            if rate is not None and (not math.isfinite(rate) or rate < 0):
                raise ValueError("Invalid LLM cost rate")
            rates.append(rate)
        return cls(provider=provider, model=env.get(prefix + "MODEL", ""),
            url=env.get(prefix + "URL", cls.url),
            api_key=env.get(prefix + "API_KEY", ""), timeout=timeout, attempts=attempts,
            max_source_chars=max_chars, input_usd_per_million=rates[0], output_usd_per_million=rates[1])


@dataclass(frozen=True)
class Completion:
    content: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    cost_basis: str = "unknown"


class LLMClient(Protocol):
    model: str

    def complete(self, messages: list[dict[str, str]]) -> Completion: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward credentials or workplace records to an unexpected endpoint.
        raise LLMError("provider_redirect")


class HTTPClient:
    def __init__(self, config: LLMConfig):
        self.config = config
        self.model = config.model

    def complete(self, messages: list[dict[str, str]]) -> Completion:
        config = self.config
        parsed = urllib.parse.urlsplit(config.url)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if not config.model or parsed.username or parsed.password or parsed.fragment or not parsed.hostname:
            raise LLMError("provider_not_configured")
        if parsed.hostname == "api.openai.com" and not config.api_key:
            raise LLMError("provider_not_configured")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
            raise LLMError("insecure_provider_url")
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"}
        if config.api_key:
            headers["Authorization"] = "Bearer " + config.api_key
        request = urllib.request.Request(config.url, headers=headers, method="POST", data=json.dumps({
            "model": config.model, "messages": messages, "response_format": {"type": "json_object"},
            "max_completion_tokens": 2048,
        }).encode("utf-8"))
        try:
            opener = urllib.request.build_opener(_NoRedirect())
            with opener.open(request, timeout=config.timeout) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise LLMError("provider_response_too_large")
            result = json.loads(raw)
            content = result["choices"][0]["message"]["content"]
            model = result.get("model", config.model)
            if not isinstance(content, str) or not isinstance(model, str) or not model:
                raise LLMError("invalid_provider_response")
            usage = result.get("usage") or {}
            tokens = [usage.get("prompt_tokens"), usage.get("completion_tokens")]
            if any(value is not None and (type(value) is not int or value < 0) for value in tokens):
                raise LLMError("invalid_provider_usage")
            cost, basis = None, "unknown"
            if all(value is not None for value in [*tokens, config.input_usd_per_million, config.output_usd_per_million]):
                cost = (tokens[0] * config.input_usd_per_million + tokens[1] * config.output_usd_per_million) / 1_000_000
                basis = "estimated_from_configured_rates"
            return Completion(content, model, *tokens, cost, basis)
        except urllib.error.HTTPError as exc:
            raise LLMError("provider_http_error", retryable=exc.code == 429 or exc.code >= 500) from None
        except (TimeoutError, socket.timeout):
            raise LLMError("provider_timeout", retryable=True) from None
        except urllib.error.URLError:
            raise LLMError("provider_unavailable", retryable=True) from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError("invalid_provider_response") from None


def client_from_config(config: LLMConfig) -> LLMClient | None:
    return None if config.provider == "none" else HTTPClient(config)
