"""Validated command inputs and official ask output shape."""

import re
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Nonempty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IngestRequest(Request):
    day: int = Field(gt=0)


class PersonRequest(Request):
    person: Nonempty


class BriefRequest(PersonRequest):
    date: date

    @field_validator("date", mode="before")
    @classmethod
    def calendar_date(cls, value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Expected YYYY-MM-DD")
        return value


class AskRequest(PersonRequest):
    at: datetime
    ask: Nonempty

    @field_validator("at", mode="before")
    @classmethod
    def utc_timestamp(cls, value: str) -> datetime:
        if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)", value
        ):
            raise ValueError("Expected an ISO UTC timestamp")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() != timedelta(0):
            raise ValueError("Timestamp must be UTC")
        return parsed


class ForgetRequest(PersonRequest):
    what: Nonempty


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    ms: int = 0


class Answer(BaseModel):
    text: str
    sources: list[str] = Field(default_factory=list)


class AskPlaceholder(BaseModel):
    answer: Answer
    matched: list[dict] = Field(default_factory=list)
    clarify: Literal[None] = None
    refused: Literal[None] = None
    usage: Usage = Field(default_factory=Usage)
