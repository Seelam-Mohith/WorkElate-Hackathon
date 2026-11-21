"""Official briefs: day-aware preparation, personalization, privacy, and model bounds."""

import json

import pytest

from conftest import record
from test_cli import invoke
from test_feedback_learning import correction
from test_memory import FakeClient, evidence
from workelate.briefs.service import generate_briefs
from workelate.ingestion.service import ingest_day
from workelate.reasoning.llm_client import Completion
from workelate.retrieval.context_builder import token_bound
from workelate.schemas.briefs import MeetingBrief
from workelate.storage.database import connect_database


def calendar(record_id="calendar", **changes):
    return record(record_id, "event", **{"event_id": record_id, "start": "2026-11-03T05:30:00Z", "at": "2026-11-02T03:00:00Z", "title": "Client One check-in", **changes})


def brief(pack, person="p1", day="2026-11-03", **kwargs):
    payload = generate_briefs(person, day, pack.settings, **kwargs)
    for item in payload:
        MeetingBrief.model_validate(item)
        assert set(item) == {"meeting_id", "status", "changed", "promises", "open_these", "question_to_ask", "usage"}
    return payload


def test_nothing_new_no_meetings_and_unknown_requester(pack):
    pack.records(1, [calendar()])
    ingest_day(1, pack.settings)
    result = brief(pack)[0]
    assert result["status"] == "nothing_new"
    assert result["changed"] == result["promises"] == result["open_these"] == []
    assert result["question_to_ask"] is None
    assert brief(pack, person="unknown") == []
    assert brief(pack, person="q1") == []
    assert brief(pack, day="2026-11-04") == []


def test_person_local_meeting_dates(pack):
    pack.records(1, [calendar(start="2026-11-03T23:30:00Z")])
    ingest_day(1, pack.settings)
    assert len(brief(pack, person="p1", day="2026-11-04")) == 1
    assert brief(pack, person="p2", day="2026-11-04") == []
    assert len(brief(pack, person="p2", day="2026-11-03")) == 1


def test_calendar_version_recorded_after_meeting_cannot_leak(pack):
    pack.records(1, [calendar(), calendar("late-calendar", event_id="calendar",
        at="2026-11-03T06:00:00Z", title="Future secret agenda"),
        record("blocked", "task", status="blocked", change={"field": "status", "from": "todo", "to": "blocked"})])
    ingest_day(1, pack.settings)
    result = brief(pack)[0]
    assert result["open_these"][0]["record"] == "calendar"
    assert "Future secret" not in json.dumps(result)


def test_changes_decisions_blockers_context_and_discussion(pack):
    pack.records(1, [calendar(), record("created", "task", at="2026-11-02T04:00:00Z"),
        record("blocked", "task", status="blocked", change={"field": "status", "from": "todo", "to": "blocked"}, at="2026-11-02T12:00:00Z"),
        record("decision", "note", event_id="calendar", body="Decision: delay the campaign.")])
    ingest_day(1, pack.settings)
    result = brief(pack)[0]
    assert result["status"] == "brief"
    assert any("blocked" in line["text"] for line in result["changed"])
    assert any("Decision: delay" in line["text"] for line in result["changed"])
    assert result["open_these"][0]["record"] == "calendar"
    assert "Meeting context" in result["open_these"][0]["why"]
    assert "unblock" in result["question_to_ask"]["text"]
    assert result["question_to_ask"]["sources"] == ["blocked"]
    assert result["usage"]["input_tokens"] == result["usage"]["output_tokens"] == 0


def test_baseline_is_previous_attended_meeting_with_same_client(pack):
    pack.records(1, [calendar(), calendar("previous", start="2026-11-02T12:00:00Z"),
        record("task", "task", status="done", change={"field": "status", "from": "todo", "to": "done"}, at="2026-11-02T10:00:00Z")])
    ingest_day(1, pack.settings)
    assert brief(pack)[0]["status"] == "nothing_new"


def test_does_not_use_another_person_last_meeting_as_the_baseline(pack):
    pack.records(1, [calendar(), calendar("others", attendees=["p2"], start="2026-11-02T12:00:00Z"),
        record("task", "task", status="done", change={"field": "status", "from": "todo", "to": "done"}, at="2026-11-02T10:00:00Z")])
    ingest_day(1, pack.settings)
    assert any("done" in item["text"] for item in brief(pack)[0]["changed"])


def test_pending_and_completed_promises_are_grounded(pack):
    pack.records(1, [calendar(), record("promise", body="I will send wireframes tomorrow"),
        record("second", body="I will send report tomorrow"), record("delivery", "mail", body="Report delivered", at="2026-11-02T14:00:00Z")])
    fake = FakeClient({"promise": {"commitments": [evidence("promise", "I will send wireframes tomorrow", description="Send wireframes", deliverable="wireframes", due_relative="tomorrow")]},
        "second": {"commitments": [evidence("second", "I will send report tomorrow", description="Send report", deliverable="report", due_relative="tomorrow")]},
        "delivery": {"deliveries": [evidence("delivery", "Report delivered", deliverable="report")]}})
    ingest_day(1, pack.settings, llm_client=fake)
    result = brief(pack)[0]
    assert result["promises"] == [{"owner": "p1", "what": "Send wireframes", "due": "2026-11-03", "sources": ["promise"]}]
    assert any("Send report" in line["text"] and "completed" in line["text"] for line in result["changed"])


def test_does_not_invent_due_date_for_undated_promise(pack):
    pack.records(1, [calendar(), record("promise", body="I will send wireframes")])
    fake = FakeClient({"promise": {"commitments": [evidence("promise", "I will send wireframes", description="Send wireframes", deliverable="wireframes")]}})
    ingest_day(1, pack.settings, llm_client=fake)
    result = brief(pack)[0]
    assert result["promises"] == []
    assert any("no due date" in item["text"] for item in result["changed"])


def test_future_days_do_not_change_historical_brief_even_before_meeting(pack):
    pack.records(1, [calendar(), record("created", "task")])
    pack.records(2, [record("next-day", "task", owner="p2", change={"field": "owner", "from": "p1", "to": "p2"}, at="2026-11-03T04:00:00Z")])
    ingest_day(1, pack.settings)
    before = brief(pack)[0]
    ingest_day(2, pack.settings)
    after = brief(pack)[0]
    assert {key: before[key] for key in before if key != "usage"} == {key: after[key] for key in after if key != "usage"}
    assert "next-day" not in json.dumps(after)


def test_source_after_meeting_is_never_used_even_if_misfiled(pack):
    pack.records(1, [calendar(), record("future", "task", status="blocked", change={"field": "status", "from": "todo", "to": "blocked"}, at="2026-11-03T06:00:00Z")])
    ingest_day(1, pack.settings)
    assert "future" not in json.dumps(brief(pack))


def test_late_utc_previous_day_work_is_not_dropped_at_india_midnight(pack):
    pack.records(1, [calendar(), record("late", "task", status="blocked", change={"field": "status", "from": "todo", "to": "blocked"}, author="p2", at="2026-11-02T23:00:00Z")])
    ingest_day(1, pack.settings)
    assert any("late" in line["sources"] for line in brief(pack)[0]["changed"])


def test_private_restricted_and_cross_org_sources_do_not_leak(pack):
    pack.records(1, [calendar(), record("public", "task", status="done", change={"field": "status", "from": "todo", "to": "done"}),
        record("private", "task", task_id="secret-task", title="SECRET CONTENT", visibility="private", author="p2"),
        record("restricted", "task", task_id="restricted-task", title="RESTRICTED CONTENT", visibility="restricted", readers=["p2"]),
        record("foreign", org="org-b", author="q1", body="FOREIGN CONTENT")])
    ingest_day(1, pack.settings)
    text = json.dumps(brief(pack))
    assert all(word not in text for word in ("SECRET CONTENT", "RESTRICTED CONTENT", "FOREIGN CONTENT", '"private"', '"restricted"'))


def test_restricted_calendar_author_has_no_implicit_access(pack):
    pack.records(1, [calendar(visibility="restricted", readers=["p2"])])
    ingest_day(1, pack.settings)
    assert brief(pack) == []
    assert len(brief(pack, person="p2")) == 1


def test_preferences_apply_after_delay_and_blockers_rank_first(pack):
    pack.records(1, [calendar("previous"), calendar("next", start="2026-11-04T05:30:00Z"), record("created", "task")])
    pack.records(2, [record("owner", "task", owner="p2", change={"field": "owner", "from": "p1", "to": "p2"}, at="2026-11-03T10:00:00Z"),
        record("blocker", "task", task_id="other-task", title="Other task", status="blocked", change={"field": "status", "from": "todo", "to": "blocked"}, at="2026-11-03T11:00:00Z")])
    pack.feedback(1, [{"day": 1, "person": "p1", "type": "preference", "words": "Max 1 change. Blockers first."}])
    # Singular change is supported too.
    ingest_day(1, pack.settings)
    ingest_day(2, pack.settings)
    result = brief(pack, day="2026-11-04")[0]
    assert len(result["changed"]) == 1
    assert "blocked" in result["changed"][0]["text"]


def test_correction_influences_later_brief_without_changing_earlier_one(pack):
    pack.records(1, [calendar("event-1"), calendar("next", start="2026-11-04T05:30:00Z"), record("baseline", "doc", body="Billing contact: Person One")])
    pack.feedback(1, [correction()])
    ingest_day(1, pack.settings)
    earlier = brief(pack)
    ingest_day(2, pack.settings)
    later = brief(pack, day="2026-11-04")[0]
    assert any("Your correction" in item["text"] and "p2" in item["text"] for item in later["changed"])
    assert "Your correction" not in json.dumps(brief(pack))


class BriefClient:
    model = "mock-brief"
    def __init__(self, invalid=False):
        self.invalid = invalid
        self.calls = []
    def complete(self, messages):
        self.calls.append(messages)
        assert token_bound(messages) <= 8000
        data = json.loads(messages[1]["content"])
        candidates = data["CANDIDATES"]
        changes = [row["id"] for row in candidates if row["category"] != "promise"][:data["preferences"]["max_changes"]]
        promises = [row["id"] for row in candidates if row["category"] == "promise"]
        if self.invalid:
            changes = ["invented-source-or-card"]
        return Completion(json.dumps({"changed_ids": changes, "promise_ids": promises, "question_id": None}), self.model, 222, 33, 0.002, "reported")


def test_model_selection_8000_budget_usage_and_cache(pack):
    pack.records(1, [calendar(), record("task", "task", status="blocked", change={"field": "status", "from": "todo", "to": "blocked"})])
    ingest_day(1, pack.settings)
    client = BriefClient()
    first = brief(pack, llm_client=client)[0]
    assert first["usage"]["input_tokens"] == 222
    assert first["usage"]["output_tokens"] == 33
    assert first["usage"]["cost_usd"] == 0.002
    second = brief(pack, llm_client=client)[0]
    assert len(client.calls) == 1
    assert second["usage"]["input_tokens"] == second["usage"]["output_tokens"] == 0
    assert first["changed"] == second["changed"]


def test_unsupported_model_selection_cannot_fabricate_brief(pack):
    pack.records(1, [calendar(), record("task", "task", status="blocked", change={"field": "status", "from": "todo", "to": "blocked"})])
    ingest_day(1, pack.settings)
    result = brief(pack, llm_client=BriefClient(invalid=True))[0]
    assert "invented" not in json.dumps(result)
    assert any("blocked" in item["text"] for item in result["changed"])
    assert result["usage"]["input_tokens"] == 222


def test_too_large_context_is_not_sent_to_model(pack):
    pack.records(1, [calendar(title="長" * 9000), record("task", "task")])
    ingest_day(1, pack.settings)
    client = BriefClient()
    assert brief(pack, llm_client=client)[0]["usage"]["input_tokens"] == 0
    assert not client.calls


class PolishClient:
    model = "mock-polish"

    def __init__(self, *, invalid=False):
        self.invalid = invalid
        self.calls = []

    def complete(self, messages):
        self.calls.append(messages)
        data = json.loads(messages[1]["content"])
        lines = data["LINES"]
        if self.invalid:
            lines = {key: value + " [invented]" for key, value in lines.items()}
        else:
            lines = {key: "Polished " + value for key, value in lines.items()}
        return Completion(json.dumps({"lines": lines}), self.model, 10, 5, 0.001, "reported")


def blocked_day_one(pack):
    pack.records(1, [calendar(), record("task", "task", status="blocked", change={"field": "status", "from": "todo", "to": "blocked"})])
    ingest_day(1, pack.settings)


def test_polish_uses_llm_and_keeps_grounded_content(pack):
    blocked_day_one(pack)
    client = PolishClient()
    result = generate_briefs("p1", "2026-11-03", pack.settings, polish=True, polish_client=client)[0]
    assert len(client.calls) == 1
    assert result["paragraph"].startswith("Polished Meeting context")
    assert "Polished" in result["paragraph"]
    assert result["usage"]["input_tokens"] == 10
    assert result["usage"]["output_tokens"] == 5
    assert result["usage"]["cost_usd"] == 0.001


def test_polish_rejects_invented_source_ids(pack):
    blocked_day_one(pack)
    result = generate_briefs("p1", "2026-11-03", pack.settings, polish=True, polish_client=PolishClient(invalid=True))[0]
    assert "[invented]" not in result["paragraph"]
    assert "Meeting context" in result["paragraph"]


def test_cli_pretty_emits_json_paragraph(pack, tmp_path):
    blocked_day_one(pack)
    env = {"R3_PACK": str(pack.root), "WORKELATE_RUNTIME_DIR": str(pack.settings.runtime_dir)}
    result, _ = invoke(["ingest", "--day", "1"], tmp_path, extra_env=env)
    assert result.returncode == 0
    result, payload = invoke(["brief", "--person", "p1", "--date", "2026-11-03", "--pretty"], tmp_path, extra_env=env)
    assert result.returncode == 0
    assert result.stderr == ""
    assert set(payload[0]) == {"meeting_id", "paragraph", "usage"}
    assert payload[0]["paragraph"]


def test_cli_runs_across_processes_and_stdout_is_json_only(pack, tmp_path):
    pack.records(1, [calendar(), record("task", "task")])
    env = {"R3_PACK": str(pack.root), "WORKELATE_RUNTIME_DIR": str(pack.settings.runtime_dir)}
    result, _ = invoke(["ingest", "--day", "1"], tmp_path, extra_env=env)
    assert result.returncode == 0
    for console in (False, True):
        result, payload = invoke(["brief", "--person", "p1", "--date", "2026-11-03"], tmp_path, entrypoint=console, extra_env=env)
        assert result.returncode == 0
        assert result.stderr == ""
        assert payload[0]["status"] == "brief"
