"""Question-relative calendar interpretation anchored only to --at and user zone."""

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from workelate.config import get_timezone
from workelate.time_utils import parse_calendar_date, parse_utc


@dataclass(frozen=True)
class TimeQuery:
    cutoff: datetime
    as_of: datetime
    since: datetime | None = None
    due_date: str | None = None
    due_relation: str = "on"


def interpret_time(question: str, at, zone: str) -> TimeQuery:
    cutoff = parse_utc(at)
    local = cutoff.astimezone(get_timezone(zone))
    words = question.casefold()
    day = None
    match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", words)
    if match:
        day = parse_calendar_date(match[0])
    else:
        for term, offset in (("yesterday", -1), ("tomorrow", 1), ("today", 0)):
            if re.search(r"\b" + term + r"\b", words):
                day = local.date() + timedelta(days=offset)
                break
        if day is None:
            for weekday, name in enumerate(("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")):
                if re.search(r"\b" + name + r"\b", words) and not re.search(r"\b" + name + r"\s+(?:thing|one)\b", words):
                    offset = (local.weekday() - weekday) % 7
                    if "last " + name in words and offset == 0:
                        offset = 7
                    day = local.date() - timedelta(days=offset)
                    break
    since = None
    as_of = cutoff
    due = day.isoformat() if day and re.search(r"\b(due|deadline)\b", words) else None
    if day:
        start = datetime.combine(day, time.min, local.tzinfo).astimezone(cutoff.tzinfo)
        end = (datetime.combine(day + timedelta(days=1), time.min, local.tzinfo) - timedelta(microseconds=1)).astimezone(cutoff.tzinfo)
        if "before" in words and "meeting" not in words and not due:
            as_of = min(cutoff, start - timedelta(microseconds=1))
        elif re.search(r"\b(since|after)\b", words) and not due:
            since = start
        elif re.search(r"\b(changed|changes|happened)\b", words) and not due:
            since = start
            as_of = min(cutoff, end)
        elif not due:
            as_of = min(cutoff, end)
    relation = "before" if due and "before" in words else "after" if due and "after" in words else "since" if due and "since" in words else "on"
    return TimeQuery(cutoff, as_of, since, due, relation)
