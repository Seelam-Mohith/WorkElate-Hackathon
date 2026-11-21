"""Small independent challenge packs; no dependency on organizer answer files."""

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from workelate.config import Settings
from workelate.ingestion.service import database_path


def record(record_id: str = "r-team", record_type: str = "chat", **overrides) -> dict:
    extra = {
        "mail": {},
        "chat": {},
        "doc": {"doc_id": "doc-1", "version": 1, "title": "Plan"},
        "sheet": {"sheet_id": "sheet-1", "version": 1, "name": "Revenue", "cells": {"A1": "42"}},
        "task": {"task_id": "task-1", "title": "Ship", "owner": "p1", "due": "2026-11-04", "status": "todo", "change": {"field": "created"}},
        "event": {"event_id": "event-1", "start": "2026-11-02T23:30:00Z", "attendees": ["p1", "p2"], "title": "Check in"},
        "note": {"event_id": "event-1"},
        "notification": {},
    }
    return {
        "id": record_id, "org": "org-a", "type": record_type,
        "at": "2026-11-02T10:00:00Z", "author": "p1", "client": "client-1",
        "visibility": "team", "body": "Private source text; never log this.",
        **extra[record_type], **overrides,
    }


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


@dataclass
class Pack:
    root: Path
    settings: Settings

    @property
    def database(self) -> Path:
        return database_path(self.settings)

    def records(self, day: int, rows: list[dict]) -> None:
        write_jsonl(self.root / "data" / "days" / f"day-{day:02d}.jsonl", rows)

    def feedback(self, day: int, rows: list[dict]) -> None:
        write_jsonl(self.root / "data" / "feedback" / f"day-{day:02d}.jsonl", rows)


@pytest.fixture
def pack(tmp_path) -> Pack:
    root = tmp_path / "pack"
    for directory in ("days", "feedback"):
        (root / "data" / directory).mkdir(parents=True)
    people = {
        "org-a": {"name": "Agency A", "people": [
            {"id": "p1", "name": "Person One", "role": "manager", "team": "client", "zone": "Asia/Kolkata"},
            {"id": "p2", "name": "Person Two", "role": "developer", "zone": "Europe/London"},
        ]},
        "org-b": {"name": "Agency B", "people": [
            {"id": "q1", "name": "Other Person", "role": "manager", "zone": "Asia/Kolkata"},
        ]},
    }
    (root / "data" / "people.json").write_text(json.dumps(people), encoding="utf-8")
    (root / "data" / "clients.json").write_text(json.dumps([{"id": "client-1", "name": "Client One"}]), encoding="utf-8")
    fixture = Pack(root, Settings(root, tmp_path / "runtime", "WARNING"))
    for day in range(1, 4):
        fixture.records(day, [record(f"r-day-{day}", at=f"2026-11-0{day + 1}T10:00:00Z")])
        fixture.feedback(day, [{"day": day, "person": "p1", "type": "preference", "words": f"Preference {day}"}])
    return fixture
