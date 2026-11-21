"""Validate raw feedback without applying any memory updates."""

from typing import Literal

from pydantic import Field, model_validator

from workelate.schemas.records import Identifier, SourceModel, Text


class Feedback(SourceModel):
    day: int = Field(gt=0)
    person: Identifier
    type: Literal["preference", "correction", "verdict", "forget"]
    words: Text
    meeting: Identifier | None = None
    topic: Text | None = None
    verdict: Literal["kept", "ignored"] | None = None

    @model_validator(mode="after")
    def required_fields(self):
        if self.type in {"correction", "verdict"} and self.meeting is None:
            raise ValueError("Meeting is required")
        if self.type == "verdict" and (self.topic is None or self.verdict is None):
            raise ValueError("Verdicts require topic and verdict")
        return self
