"""Side-effect-free configuration; relative paths resolve against the working directory."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo
from workelate.reasoning.llm_client import LLMConfig
from workelate.retrieval.embeddings import SearchConfig


@dataclass(frozen=True)
class Settings:
    pack_dir: Path
    runtime_dir: Path
    log_level: str
    llm: LLMConfig = field(default_factory=LLMConfig)
    polish_llm: LLMConfig = field(default_factory=LLMConfig)
    search: SearchConfig = field(default_factory=SearchConfig)


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if environ is None else environ
    cwd = Path.cwd()
    pack = Path(env.get("R3_PACK") or cwd).expanduser().resolve()
    runtime = Path(env.get("WORKELATE_RUNTIME_DIR") or cwd / ".workelate").expanduser().resolve()
    level = env.get("WORKELATE_LOG_LEVEL", "WARNING").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError("Invalid logging level")
    return Settings(pack, runtime, level, LLMConfig.from_env(env),
                    LLMConfig.from_env(env, "WORKELATE_POLISH_LLM_"), SearchConfig.from_env(env))


def get_timezone(name: str) -> ZoneInfo:
    """Load an IANA timezone for future person-local date handling."""
    return ZoneInfo(name)
