"""Exercise the installed CLI in separate processes, outside the source tree."""

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from workelate.config import get_timezone, load_settings

VALID = [
    ["ingest", "--day", "1"],
    ["brief", "--person", "p02", "--date", "2026-11-03"],
    ["ask", "--person", "p02", "--at", "2026-11-04T18:00:00Z", "--ask", "Who owns billing?"],
    ["forget", "--person", "p02", "--what", "my travel preference"],
]


def invoke(args, cwd, entrypoint=False, extra_env=None):
    env = os.environ.copy()
    for key in ("R3_PACK", "WORKELATE_RUNTIME_DIR", "WORKELATE_LOG_LEVEL", "PYTHONPATH"):
        env.pop(key, None)
    env.update(extra_env or {})
    executable = Path(sys.executable).parent / ("assist.exe" if os.name == "nt" else "assist")
    prefix = [str(executable)] if entrypoint else [sys.executable, "-m", "workelate.cli"]
    result = subprocess.run(prefix + args, cwd=cwd, env=env, capture_output=True, text=True, timeout=20)
    payload = json.loads(result.stdout)  # Also rejects extra JSON values or log lines.
    assert "Traceback" not in result.stderr
    return result, payload


@pytest.mark.parametrize("args", VALID[1:])
@pytest.mark.parametrize("entrypoint", [False, True])
def test_valid_commands_are_honest_placeholders(args, entrypoint, tmp_path):
    result, payload = invoke(args, tmp_path, entrypoint)
    assert result.returncode == 3
    assert "not implemented" in result.stderr
    assert "WARNING" not in result.stdout
    assert "ok" not in payload
    if args[0] == "brief":
        assert payload == []
    elif args[0] == "ask":
        assert set(payload) == {"answer", "matched", "clarify", "refused", "usage"}
        assert payload["answer"] == {"text": "Question answering is not implemented in Stage 1.", "sources": []}
        assert payload["matched"] == []
        assert payload["clarify"] is payload["refused"] is None
        assert payload["usage"] == {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "ms": 0}
    else:
        assert payload == {"error": "not_implemented", "command": args[0]}
    assert list(tmp_path.iterdir()) == []


MISSING = [args[:i] + args[i + 2:] for args in VALID for i in range(1, len(args), 2)]


@pytest.mark.parametrize("args", MISSING + [[], ["unknown"], ["ingest", "--day", "1", "--extra", "secret"]])
def test_missing_and_unknown_arguments(args, tmp_path):
    result, payload = invoke(args, tmp_path)
    assert result.returncode == 2
    assert payload["error"] == "validation_error"


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "abc"])
def test_invalid_days(value, tmp_path):
    result, payload = invoke(["ingest", "--day", value], tmp_path)
    assert result.returncode == 2
    assert payload["error"] == "validation_error"


@pytest.mark.parametrize("value", ["2026-02-30", "2026-13-01", "20261103", "03-11-2026", "2026-11-03T00:00:00Z"])
def test_invalid_dates(value, tmp_path):
    result, _ = invoke(["brief", "--person", "p02", "--date", value], tmp_path)
    assert result.returncode == 2


@pytest.mark.parametrize("value", ["2026-11-04", "2026-11-04T18:00:00", "2026-11-04T18:00:00+05:30", "2026-02-30T18:00:00Z", "2026-11-04T25:00:00Z", "garbage"])
def test_invalid_timestamps(value, tmp_path):
    args = VALID[2].copy()
    args[4] = value
    result, _ = invoke(args, tmp_path)
    assert result.returncode == 2


@pytest.mark.parametrize("value", ["2026-11-04T18:00:00+00:00", "2026-11-04T18:00:00.123Z"])
def test_valid_utc_forms(value, tmp_path):
    args = VALID[2].copy()
    args[4] = value
    result, _ = invoke(args, tmp_path)
    assert result.returncode == 3


@pytest.mark.parametrize("index", [2, 3])
@pytest.mark.parametrize("value", ["", "   "])
def test_empty_question_or_forget(index, value, tmp_path):
    args = VALID[index].copy()
    args[-1] = value
    result, _ = invoke(args, tmp_path)
    assert result.returncode == 2


def test_empty_person(tmp_path):
    result, _ = invoke(["brief", "--person", " ", "--date", "2026-11-03"], tmp_path)
    assert result.returncode == 2


def test_no_private_input_in_diagnostics(tmp_path):
    secret = "PRIVATE REQUEST CONTENT"
    result, _ = invoke(["ask", "--person", "p02", "--at", secret, "--ask", secret], tmp_path)
    assert secret not in result.stdout + result.stderr


@pytest.mark.parametrize("args", [["--help"], ["ask", "--help"]])
def test_help_is_json(args, tmp_path):
    result, payload = invoke(args, tmp_path)
    assert result.returncode == 0
    assert "usage:" in payload["help"]


def test_log_level(tmp_path):
    result, _ = invoke(VALID[3], tmp_path, extra_env={"WORKELATE_LOG_LEVEL": "ERROR"})
    assert result.stderr == ""
    assert result.returncode == 3


def test_invalid_configuration_is_safe(tmp_path):
    result, payload = invoke(VALID[0], tmp_path, extra_env={"WORKELATE_LOG_LEVEL": "PRIVATE INVALID LEVEL"})
    assert result.returncode == 2
    assert payload == {"error": "configuration_error"}
    assert "PRIVATE" not in result.stdout + result.stderr


def test_config_paths(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    settings = load_settings({"R3_PACK": "pack/../challenge", "WORKELATE_RUNTIME_DIR": "runtime"})
    assert settings.pack_dir == (tmp_path / "challenge").resolve()
    assert settings.runtime_dir == (tmp_path / "runtime").resolve()
    assert not settings.runtime_dir.exists()
    monkeypatch.setenv("R3_PACK", str(tmp_path / "actual-pack"))
    assert load_settings().pack_dir == (tmp_path / "actual-pack").resolve()


def test_cwd_fallback(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert load_settings({}).pack_dir == tmp_path.resolve()
    assert load_settings({}).runtime_dir == (tmp_path / ".workelate").resolve()


def test_timezones():
    assert get_timezone("Asia/Kolkata").key == "Asia/Kolkata"
    assert get_timezone("Europe/London").key == "Europe/London"


@pytest.mark.parametrize("module", ["cli", "config", "logging_config", "schemas", "ingestion", "storage", "memory", "retrieval", "reasoning", "briefs", "privacy"])
def test_imports(module):
    importlib.import_module(f"workelate.{module}")


def test_unexpected_failure_is_sanitized(monkeypatch, capsys):
    from workelate import cli

    def fail(*args):
        raise RuntimeError("private data must stay hidden")

    monkeypatch.setattr(cli, "execute", fail)
    assert cli.main(VALID[3]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"error": "internal_error"}
    assert "private data" not in captured.out + captured.err
