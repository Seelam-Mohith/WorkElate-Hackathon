"""Translate validation failures into safe source locations."""

from pydantic import ValidationError

from workelate.ingestion.errors import IngestionError


def validate(adapter, value, source: str, line: int | None = None):
    try:
        return adapter.validate_python(value, strict=True) if hasattr(adapter, "validate_python") else adapter.model_validate(value)
    except (ValidationError, ValueError, TypeError) as exc:
        raise IngestionError("invalid_source_data", source=source, line=line) from exc
