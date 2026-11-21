"""Date options obey the same person, timezone and ingestion scope as results."""

from conftest import record
from workelate.briefs.service import generate_briefs
from workelate.forgetting.service import forget_information
from workelate.ingestion.service import ingest_day
from workelate.ui_options import person_dates


def test_empty_and_unknown_people_have_no_dates(pack):
    assert person_dates("p1", pack.settings)["brief_dates"] == []
    assert not pack.database.exists()
    ingest_day(1, pack.settings)
    assert person_dates("unknown", pack.settings)["ask_dates"] == []


def test_dates_follow_person_timezone_permissions_and_available_days(pack):
    pack.records(1, [record("visible"),
        record("meeting", "event", start="2026-11-03T23:30:00Z"),
        record("private", "event", event_id="private", visibility="private", start="2026-11-05T10:00:00Z"),
        record("foreign", "event", event_id="foreign", org="org-b", author="q1", start="2026-11-06T10:00:00Z"),
        record("restricted", "event", event_id="restricted", visibility="restricted", readers=["p2"], start="2026-11-07T10:00:00Z"),
        record("future", at="2026-11-10T10:00:00Z")])
    ingest_day(1, pack.settings)
    one = person_dates("p1", pack.settings)
    two = person_dates("p2", pack.settings)
    assert one["timezone"] == "Asia/Kolkata"
    assert one["brief_dates"] == ["2026-11-04", "2026-11-05"]
    assert two["brief_dates"] == ["2026-11-03", "2026-11-07"]
    assert one["record_dates"] == ["2026-11-02"]
    assert one["ask_dates"] == [{"date": "2026-11-02", "at": "2026-11-02T18:29:59.999999Z"}]
    assert two["ask_dates"] == [{"date": "2026-11-02", "at": "2026-11-02T23:59:59.999999Z"}]
    for person in ("p1", "p2"):
        for day in person_dates(person, pack.settings)["brief_dates"]:
            assert generate_briefs(person, day, pack.settings)


def test_calendar_updates_and_forgetting_remove_dates(pack):
    pack.records(1, [record("old", "event", event_id="meeting", start="2026-11-03T23:30:00Z")])
    ingest_day(1, pack.settings)
    assert person_dates("p1", pack.settings)["brief_dates"] == ["2026-11-04"]
    pack.records(2, [record("updated", "event", event_id="meeting", start="2026-11-03T23:30:00Z",
                            at="2026-11-03T10:00:00Z", attendees=["p2"])])
    ingest_day(2, pack.settings)
    assert person_dates("p1", pack.settings)["brief_dates"] == []
    assert person_dates("p1", pack.settings)["record_dates"] == ["2026-11-02", "2026-11-03"]
    forget_information("p1", "forget all", pack.settings)
    assert person_dates("p1", pack.settings)["record_dates"] == []
