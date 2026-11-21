"""Permission-filtered source retrieval. Ingestion never uses this repository.

These methods return original source objects, without summarizing or resolving facts.
The connection is injected by trusted application code, not supplied by end users.
"""

import json
import sqlite3
from datetime import date as CalendarDate, datetime

from workelate.privacy.access import ACCESS_SQL
from workelate.time_utils import normalized_utc, parse_calendar_date, utc_to_local_date


class Repository:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def get_person(self, person_id: str) -> dict | None:
        row = self._connection.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
        return dict(row) if row else None

    def _records(self, person_id: str, conditions: list[str], params: list) -> list[dict]:
        query = "SELECT r.raw_json FROM records r JOIN people p ON p.id = ? WHERE " + ACCESS_SQL
        if conditions:
            query += " AND " + " AND ".join(conditions)
        query += " ORDER BY r.occurred_at, r.id"
        return [json.loads(row[0]) for row in self._connection.execute(query, [person_id, *params])]

    def get_record(self, record_id: str, requesting_person_id: str) -> dict | None:
        rows = self._records(requesting_person_id, ["r.id = ?"], [record_id])
        return rows[0] if rows else None

    def get_accessible_records(
        self, person_id: str, until: str | datetime | None = None, client_id: str | None = None
    ) -> list[dict]:
        conditions, params = [], []
        if until is not None:
            conditions.append("r.occurred_at <= ?")
            params.append(normalized_utc(until))
        if client_id is not None:
            conditions.append("r.client_id = ?")
            params.append(client_id)
        return self._records(person_id, conditions, params)

    def get_records_for_client(
        self, person_id: str, client_id: str, until: str | datetime | None = None
    ) -> list[dict]:
        return self.get_accessible_records(person_id, until, client_id)

    def get_events_for_person(self, person_id: str, date: str | CalendarDate | None = None) -> list[dict]:
        person = self.get_person(person_id)
        if person is None:
            return []
        local_date = parse_calendar_date(date) if isinstance(date, str) else date
        events = self._records(person_id, ["r.record_type = 'event'"], [])
        return [event for event in events if person_id in event["attendees"] and (
            local_date is None or utc_to_local_date(event["start"], person["timezone"]) == local_date
        )]
