"""Read only explicitly requested source files; no directory globbing or read-ahead."""

import json
import math
from collections.abc import Iterator
from pathlib import Path

from workelate.ingestion.errors import IngestionError


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("Non-finite JSON number")


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite JSON number")
    return result


def parse_json(text: str):
    return json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant, parse_float=_float)


def read_json(path: Path):
    try:
        return parse_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise IngestionError("missing_source_file", source=path.name) from exc
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise IngestionError("invalid_source_json", source=path.name) from exc
    except OSError as exc:
        raise IngestionError("source_read_error", source=path.name) from exc


def read_jsonl(path: Path) -> Iterator[tuple[int, dict, str]]:
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    value = parse_json(line)
                    if not isinstance(value, dict):
                        raise ValueError("Expected a JSON object")
                except (ValueError, RecursionError) as exc:
                    raise IngestionError("invalid_source_json", source=path.name, line=line_number) from exc
                yield line_number, value, line.rstrip("\r\n")
    except FileNotFoundError as exc:
        raise IngestionError("missing_source_file", source=path.name) from exc
    except UnicodeError as exc:
        raise IngestionError("invalid_source_json", source=path.name) from exc
    except OSError as exc:
        raise IngestionError("source_read_error", source=path.name) from exc


def canonical_json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
