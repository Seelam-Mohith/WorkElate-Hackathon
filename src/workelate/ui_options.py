"""Person-specific date options for the local UI, derived from authorized records."""

import json
from datetime import datetime, time, timedelta, timezone

from workelate.config import get_timezone
from workelate.feedback.service import DAY_ONE
from workelate.ingestion.service import database_path
from workelate.privacy.access import ACCESS_SQL
from workelate.storage.database import connect_database
from workelate.storage.repository import Repository
from workelate.time_utils import normalized_utc, parse_utc, utc_to_local_date


def person_dates(person_id, settings):
    result = {"person": person_id, "timezone": None, "brief_dates": [], "ask_dates": [], "record_dates": []}
    path = database_path(settings)
    if not path.exists():
        return result
    with connect_database(path, read_only=True) as conn:
        person = Repository(conn).get_person(person_id)
        if person is None:
            return result
        zone = person["timezone"]
        result["timezone"] = zone
        latest = conn.execute("SELECT MAX(day) FROM ingestion_history").fetchone()[0] or 0
        if not latest:
            return result
        rows = conn.execute("SELECT r.raw_json,r.source_day FROM records r JOIN people p ON p.id=? WHERE "
                            + ACCESS_SQL + " AND r.source_day<=? ORDER BY r.occurred_at,r.id", (person_id, latest)).fetchall()
        records = [(json.loads(row[0]), row[1]) for row in rows]
        last_day = DAY_ONE + timedelta(days=latest - 1)
        dates = sorted({utc_to_local_date(record["at"], zone) for record, source_day in records
                        if DAY_ONE + timedelta(days=source_day - 1) <= utc_to_local_date(record["at"], zone) <= last_day})
        result["record_dates"] = [day.isoformat() for day in dates]
        result["ask_dates"] = [{"date": day.isoformat(), "at": normalized_utc(
            datetime.combine(day + timedelta(days=1), time.min, get_timezone(zone)).astimezone(timezone.utc)
            - timedelta(microseconds=1))} for day in dates]

        # Mirror brief generation's day limit and calendar version selection so a
        # stale event date or an update that removes the attendee is not offered.
        meeting_days = sorted({utc_to_local_date(record["start"], zone) for record, _ in records
                               if record["type"] == "event"})
        for day in meeting_days:
            through_day = min(latest, (day - DAY_ONE).days)
            if through_day < 1:
                continue
            versions = {}
            for record, source_day in records:
                if source_day <= through_day and record["type"] == "event" and parse_utc(record["at"]) < parse_utc(record["start"]):
                    versions[record["event_id"]] = record
            if any(person_id in record["attendees"] and utc_to_local_date(record["start"], zone) == day
                   for record in versions.values()):
                result["brief_dates"].append(day.isoformat())
    return result
