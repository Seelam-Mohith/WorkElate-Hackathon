"""Source validation; unknown fields remain allowed and raw input is stored separately."""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints,
    TypeAdapter, field_validator, model_validator,
)

from workelate.config import get_timezone
from workelate.time_utils import parse_calendar_date, parse_utc

Identifier = Annotated[str, StringConstraints(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")]
Text = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
UTC = Annotated[datetime, BeforeValidator(parse_utc)]
CalendarDate = Annotated[date, BeforeValidator(parse_calendar_date)]


class SourceModel(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)


class Person(SourceModel):
    id: Identifier
    name: Text
    role: Text
    team: Text | None = None
    zone: Text

    @field_validator("zone")
    @classmethod
    def valid_zone(cls, value: str) -> str:
        try:
            get_timezone(value)
        except (KeyError, ValueError) as exc:
            raise ValueError("Invalid IANA timezone") from exc
        return value


class Organization(SourceModel):
    name: Text
    people: list[Person]


class Client(SourceModel):
    id: Identifier
    name: Text


class Record(SourceModel):
    id: Identifier
    org: Identifier
    at: UTC
    author: Text  # External addresses and system are valid; no people foreign key.
    client: Identifier | None
    visibility: Literal["team", "private", "restricted"]
    readers: list[Identifier] = Field(default_factory=list)
    body: str

    @model_validator(mode="after")
    def restricted_readers(self):
        if self.visibility == "restricted" and "readers" not in self.model_fields_set:
            raise ValueError("Restricted records must specify readers")
        return self


class Mail(Record):
    type: Literal["mail"]
    to: list[Text] | None = None
    subject: str | None = None


class Chat(Record):
    type: Literal["chat"]
    channel: str | None = None


class Document(Record):
    type: Literal["doc"]
    doc_id: Identifier
    version: int = Field(gt=0)
    title: Text


class Sheet(Record):
    type: Literal["sheet"]
    sheet_id: Identifier
    version: int = Field(gt=0)
    name: Text
    cells: dict[str, str]


class TaskChange(SourceModel):
    field: Literal["created", "comment", "due", "owner", "status"]

    @model_validator(mode="after")
    def changed_values(self):
        if self.field in {"due", "owner", "status"}:
            extra = self.model_extra or {}
            if "from" not in extra or "to" not in extra:
                raise ValueError("Changes require from and to")
            for key in ("from", "to"):
                value = extra[key]
                if self.field == "due":
                    parse_calendar_date(value)
                elif self.field == "owner":
                    TypeAdapter(Identifier).validate_python(value, strict=True)
                elif value not in {"todo", "in_progress", "blocked", "done", "cancelled"}:
                    raise ValueError("Invalid task status change")
        return self


class Task(Record):
    type: Literal["task"]
    task_id: Identifier
    title: Text
    owner: Identifier
    due: CalendarDate
    status: Literal["todo", "in_progress", "blocked", "done", "cancelled"]
    change: TaskChange


class Event(Record):
    type: Literal["event"]
    event_id: Identifier
    start: UTC
    attendees: list[Text]
    title: Text


class Note(Record):
    type: Literal["note"]
    event_id: Identifier


class Notification(Record):
    type: Literal["notification"]
    to: list[Text] | None = None


RecordAdapter = TypeAdapter(Annotated[
    Mail | Chat | Document | Sheet | Task | Event | Note | Notification,
    Field(discriminator="type"),
])
OrganizationsAdapter = TypeAdapter(dict[Identifier, Organization])
ClientsAdapter = TypeAdapter(list[Client])
