"""Restricted interpretation schema: feedback cannot grant access or delete data."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    max_changes: int | None = Field(default=None, ge=1, le=20)
    blockers_first: bool | None = None
    skip_newsletters: bool | None = None
    my_tasks_first: bool | None = None


class Correction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    subject_type: Literal["client", "task"]
    subject_id: str
    predicate: Literal["billing_owner", "launch_date", "owner", "due_date", "status"]
    old_value: JsonValue
    value: JsonValue


class InterpretedFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    preferences: Preferences | None = None
    correction: Correction | None = None
