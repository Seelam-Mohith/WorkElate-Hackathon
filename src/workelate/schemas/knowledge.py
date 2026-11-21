"""Strict internal semantic extraction format; never an official CLI answer schema."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from workelate.schemas.records import Identifier, Text


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_ids: list[Identifier] = Field(min_length=1)
    evidence_quote: Text
    confidence: float = Field(default=0.8, ge=0, le=1)


class Fact(Evidence):
    subject_type: Literal["client", "person", "task", "document", "sheet", "meeting"]
    subject_id: Identifier
    predicate: Literal["billing_owner", "launch_date", "owner", "due_date", "status", "decision", "alias"]
    value: JsonValue
    effective_at: str | None = None
    replaces_value: JsonValue = None


class Promise(Evidence):
    description: Text
    deliverable: Text
    owner_id: str | None = None
    client_id: Identifier | None = None
    due_date: str | None = None
    due_relative: Literal["today", "tomorrow"] | None = None


class Delivery(Evidence):
    deliverable: Text
    owner_id: str | None = None
    client_id: Identifier | None = None


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    facts: list[Fact] = Field(default_factory=list)
    decisions: list[Fact] = Field(default_factory=list)
    corrections: list[Fact] = Field(default_factory=list)
    commitments: list[Promise] = Field(default_factory=list)
    deliveries: list[Delivery] = Field(default_factory=list)
    aliases: list[Fact] = Field(default_factory=list)
