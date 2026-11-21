"""HTTP provider contract tested with an in-memory transport, never live calls."""

import json
import urllib.error
import urllib.request
from dataclasses import replace

import pytest

from workelate.reasoning.llm_client import HTTPClient, LLMConfig, LLMError


class Reply:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        return self.data[:limit]


class Transport:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return Reply(self.result)


@pytest.fixture
def config():
    return LLMConfig(provider="openai-compatible", model="test-model", api_key="test-key", timeout=7)


def transport(monkeypatch, output):
    fake = Transport(output if isinstance(output, (bytes, Exception)) else json.dumps(output).encode())
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: fake)
    return fake


def response(**extra):
    return {"model": "reported-model", "choices": [{"message": {"content": '{"facts":[]}'}}], **extra}


def test_http_shape_timeout_usage_and_estimated_cost(config, monkeypatch):
    fake = transport(monkeypatch, response(usage={"prompt_tokens": 123, "completion_tokens": 45}))
    client = HTTPClient(replace(config, input_usd_per_million=1, output_usd_per_million=2))
    output = client.complete([{"role": "user", "content": "JSON please"}])
    assert output.input_tokens == 123
    assert output.output_tokens == 45
    assert output.model == "reported-model"
    assert output.cost_usd == pytest.approx(0.000213)
    assert output.cost_basis == "estimated_from_configured_rates"
    request, timeout = fake.calls[0]
    assert timeout == 7
    assert request.get_header("Authorization") == "Bearer test-key"
    assert json.loads(request.data)["response_format"] == {"type": "json_object"}


def test_missing_usage_is_unknown_not_fabricated(config, monkeypatch):
    transport(monkeypatch, response())
    output = HTTPClient(config).complete([])
    assert output.input_tokens is output.output_tokens is output.cost_usd is None
    assert output.cost_basis == "unknown"


@pytest.mark.parametrize("error,code,retryable", [
    (TimeoutError("private contents"), "provider_timeout", True),
    (urllib.error.URLError("private contents"), "provider_unavailable", True),
    (urllib.error.HTTPError("https://example.com", 429, "private contents", {}, None), "provider_http_error", True),
    (urllib.error.HTTPError("https://example.com", 401, "private contents", {}, None), "provider_http_error", False),
])
def test_safe_transport_errors(config, monkeypatch, error, code, retryable):
    transport(monkeypatch, error)
    with pytest.raises(LLMError) as captured:
        HTTPClient(config).complete([])
    assert str(captured.value) == code
    assert captured.value.retryable is retryable
    assert "private contents" not in str(captured.value)


@pytest.mark.parametrize("output", [b"not json", {}, {"choices": []},
    response(usage={"prompt_tokens": -1}), response(usage={"completion_tokens": True}),
    response(model=[]), {"choices": [{"message": {"content": None}}]},
])
def test_invalid_provider_responses(config, monkeypatch, output):
    transport(monkeypatch, output)
    with pytest.raises(LLMError):
        HTTPClient(config).complete([])


@pytest.mark.parametrize("url", ["http://example.com/api", "https://user:secret@example.com/api", "file:///tmp/data"])
def test_insecure_provider_endpoint_rejected(config, monkeypatch, url):
    fake = transport(monkeypatch, response())
    with pytest.raises(LLMError):
        HTTPClient(replace(config, url=url)).complete([])
    assert fake.calls == []


def test_local_provider_can_work_without_api_key(config, monkeypatch):
    fake = transport(monkeypatch, response())
    HTTPClient(replace(config, url="http://localhost:1234/v1/chat/completions", api_key="")).complete([])
    assert fake.calls[0][0].get_header("Authorization") is None


def test_redirects_cannot_forward_workplace_data(config):
    from workelate.reasoning.llm_client import _NoRedirect

    with pytest.raises(LLMError, match="provider_redirect"):
        _NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://other.example")


def test_config_env_credentials_not_in_repr():
    config = LLMConfig.from_env({"WORKELATE_LLM_PROVIDER": "openai-compatible", "WORKELATE_LLM_MODEL": "chosen-model",
        "WORKELATE_LLM_API_KEY": "SECRET_API_KEY", "WORKELATE_LLM_TIMEOUT": "12", "WORKELATE_LLM_ATTEMPTS": "3"})
    assert config.model == "chosen-model"
    assert config.api_key == "SECRET_API_KEY"
    assert config.timeout == 12
    assert config.attempts == 3
    assert "SECRET_API_KEY" not in repr(config)


@pytest.mark.parametrize("env", [
    {"WORKELATE_LLM_PROVIDER": "other"}, {"WORKELATE_LLM_TIMEOUT": "0"},
    {"WORKELATE_LLM_TIMEOUT": "nan"}, {"WORKELATE_LLM_ATTEMPTS": "5"},
    {"WORKELATE_LLM_INPUT_USD_PER_MILLION": "-1"}, {"WORKELATE_LLM_MAX_SOURCE_CHARS": "0"},
])
def test_config_limits(env):
    with pytest.raises(ValueError):
        LLMConfig.from_env(env)
