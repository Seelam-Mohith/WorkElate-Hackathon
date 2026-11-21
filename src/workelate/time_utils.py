"""UTC parsing and explicit IANA timezone conversion, independent of host timezone."""

import re
from datetime import date, datetime, timedelta, timezone

from workelate.config import get_timezone


def parse_utc(value: str | datetime) -> datetime:
    if isinstance(value, str):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", value):
            raise ValueError("Expected an ISO UTC timestamp")
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("Expected a timezone-aware UTC timestamp")
    return value.astimezone(timezone.utc)


def normalized_utc(value: str | datetime) -> str:
    # Fixed precision gives SQLite text comparisons the same order as UTC time.
    return parse_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def utc_to_local_date(value: str | datetime, timezone_name: str) -> date:
    return parse_utc(value).astimezone(get_timezone(timezone_name)).date()


def parse_calendar_date(value: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Expected YYYY-MM-DD")
    return date.fromisoformat(value)
