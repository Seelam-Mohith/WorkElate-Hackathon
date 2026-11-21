"""Exact official meeting brief fields, plus an internal verified selection format."""

from typing import Literal

from pydantic import Field, model_validator

from workelate.schemas.answers import AskUsage, StrictModel
from workelate.time_utils import parse_calendar_date


class BriefLine(StrictModel):
    text: str
    sources: list[str] = Field(min_length=1)


class BriefPromise(StrictModel):
    owner: str
    what: str
    due: str
    sources: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def calendar_due(self):
        parse_calendar_date(self.due)
        return self


class OpenRecord(StrictModel):
    record: str
    why: str


class MeetingBrief(StrictModel):
    meeting_id: str
    status: Literal["brief", "nothing_new"]
    changed: list[BriefLine]
    promises: list[BriefPromise]
    open_these: list[OpenRecord]
    question_to_ask: BriefLine | None
    usage: AskUsage

    @model_validator(mode="after")
    def no_filler(self):
        if self.status == "nothing_new" and (self.changed or self.promises or self.open_these or self.question_to_ask):
            raise ValueError("Nothing-new briefs must have empty content")
        return self


class BriefSelection(StrictModel):
    changed_ids: list[str] = Field(max_length=20)
    promise_ids: list[str] = Field(max_length=20)
    question_id: str | None = None
