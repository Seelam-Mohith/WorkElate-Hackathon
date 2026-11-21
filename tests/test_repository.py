"""Permission-filtered repository and UTC/local-calendar behavior."""

from datetime import date, datetime, timezone

import pytest

from conftest import record
from workelate.ingestion.errors import IngestionError
from workelate.ingestion.service import ingest_day
from workelate.storage.database import connect_database
from workelate.storage.repository import Repository
from workelate.time_utils import normalized_utc, parse_utc, utc_to_local_date


@pytest.fixture
def repository(pack):
    pack.records(1, [
        record("team"), record("private", visibility="private"),
        record("restricted", visibility="restricted", readers=["p2", "q1"]),
        record("other-org", org="org-b", author="q1"),
        record("deny-all", visibility="restricted", readers=[]),
        record("event", "event"),
        record("future", at="2026-11-02T23:59:00.100001Z"),
    ])
    ingest_day(1, pack.settings)
    with connect_database(pack.database, read_only=True) as conn:
        yield Repository(conn)


def test_team_and_private_access(repository):
    assert repository.get_record("team", "p1")
    assert repository.get_record("team", "p2")
    assert repository.get_record("private", "p1")
    assert repository.get_record("private", "p2") is None


def test_restricted_readers_only_author_not_implicit(repository):
    assert repository.get_record("restricted", "p2")
    assert repository.get_record("restricted", "p1") is None
    assert repository.get_record("restricted", "q1") is None
    assert repository.get_record("deny-all", "p1") is None


def test_cross_org_unknown_and_missing_denied(repository):
    assert repository.get_record("other-org", "p1") is None
    assert repository.get_record("team", "q1") is None
    assert repository.get_record("private", "unknown") is None
    assert repository.get_record("team", "unknown") is None
    assert repository.get_record("missing", "p1") is None
    assert repository.get_accessible_records("unknown") == []
    assert repository.get_events_for_person("unknown") == []
    assert repository.get_person("unknown") is None


def test_filters_never_bypass_permissions(repository):
    assert {r["id"] for r in repository.get_accessible_records("p2")} == {"team", "restricted", "event", "future"}
    assert {r["id"] for r in repository.get_records_for_client("q1", "client-1")} == {"other-org"}
    assert repository.get_accessible_records("p1", client_id="nonexistent") == []
    assert repository.get_person("p2")["timezone"] == "Europe/London"
    assert repository.get_record("team' OR 1=1 --", "p1") is None


def test_utc_bounds_are_inclusive_with_fractional_seconds(repository):
    before = repository.get_accessible_records("p2", until="2026-11-02T23:59:00.100000Z")
    assert "future" not in {r["id"] for r in before}
    boundary = repository.get_records_for_client("p2", "client-1", until="2026-11-02T23:59:00.100001+00:00")
    assert "future" in {r["id"] for r in boundary}
    with pytest.raises(ValueError):
        repository.get_accessible_records("p2", until=datetime(2026, 11, 2))


def test_local_meeting_dates(repository):
    assert [r["id"] for r in repository.get_events_for_person("p1", "2026-11-03")] == ["event"]
    assert repository.get_events_for_person("p1", "2026-11-02") == []
    assert [r["id"] for r in repository.get_events_for_person("p2", date(2026, 11, 2))] == ["event"]
    assert repository.get_events_for_person("q1") == []


def test_private_events_stay_private(pack):
    pack.records(1, [record("event-private", "event", visibility="private", attendees=["p1", "p2"])])
    ingest_day(1, pack.settings)
    with connect_database(pack.database, read_only=True) as conn:
        repo = Repository(conn)
        assert repo.get_events_for_person("p2") == []
        assert len(repo.get_events_for_person("p1")) == 1


def test_restricted_reader_list_is_required(pack):
    pack.records(1, [record(visibility="restricted")])
    with pytest.raises(IngestionError, match="invalid_source_data"):
        ingest_day(1, pack.settings)


def test_reader_integrity_and_deduplication(pack):
    pack.records(1, [record(visibility="restricted", readers=["p2", "p2"])])
    ingest_day(1, pack.settings)
    with connect_database(pack.database, read_only=True) as conn:
        assert conn.execute("SELECT count(*) FROM record_readers").fetchone()[0] == 1
    pack.records(2, [record("bad-readers", visibility="restricted", readers=["missing-person"])])
    with pytest.raises(IngestionError, match="source_integrity_error"):
        ingest_day(2, pack.settings)


def test_utc_parsing_and_normalization():
    value = parse_utc("2026-11-02T23:30:00Z")
    assert value.tzinfo == timezone.utc
    assert normalized_utc(value) == "2026-11-02T23:30:00.000000Z"
    assert utc_to_local_date(value, "Asia/Kolkata") == date(2026, 11, 3)
    assert utc_to_local_date(value, "Europe/London") == date(2026, 11, 2)
    # zoneinfo handles London daylight saving, rather than a fixed UTC offset.
    assert utc_to_local_date("2026-07-02T23:30:00Z", "Europe/London") == date(2026, 7, 3)


@pytest.mark.parametrize("value", ["2026-11-02", "2026-11-02T23:30:00", "2026-11-02T23:30:00+01:00", "2026-02-30T00:00:00Z"])
def test_invalid_utc_values(value):
    with pytest.raises(ValueError):
        parse_utc(value)
