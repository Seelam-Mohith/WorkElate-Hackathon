"""Milestone D: CLI restarts, embedding HTTP, whole-ask budgets, and regressions."""

import json
import urllib.request
from dataclasses import replace

import pytest

from conftest import record
from test_answers import AT, SelectingClient, ask
from test_cli import invoke
from test_llm_client import Reply, Transport
from test_memory import FakeClient, evidence
from test_search_infrastructure import MockEmbedder
from workelate.ingestion.service import ingest_day
from workelate.reasoning.answer_engine import ask_question
from workelate.reasoning.citation_validator import validate_card
from workelate.reasoning.evidence import Card
from workelate.retrieval.embeddings import EmbeddingError, EmbeddingResult, HTTPEmbedder, SearchConfig
from workelate.retrieval.fts import keyword_search
from workelate.retrieval.indexing import index_records
from workelate.storage.database import connect_database


def test_ingest_three_days_and_ask_across_cli_restarts(pack, tmp_path):
    for day in (1, 2, 3):
        pack.records(day, [record(f"day-{day}", "task", owner="p1" if day == 1 else "p2",
            change={"field": "created"} if day == 1 else {"field": "owner", "from": "p1" if day == 2 else "p2", "to": "p2"}, at=f"2026-11-0{day+1}T10:00:00Z")])
        env = {"R3_PACK": str(pack.root), "WORKELATE_RUNTIME_DIR": str(pack.settings.runtime_dir)}
        ingested, payload = invoke(["ingest", "--day", str(day)], tmp_path, extra_env=env)
        assert ingested.returncode == 0
        assert payload["indexing"]["fts_completed"] == day
        for console in (False, True):
            result, answer = invoke(["ask", "--person", "p1", "--at", AT, "--ask", "Who owns task-1?"], tmp_path, entrypoint=console, extra_env=env)
            assert result.returncode == 0
            assert ("p1" if day == 1 else "p2") in answer["answer"]["text"]
            assert result.stderr == ""
    past = ask(pack, "Who owns task-1?", at="2026-11-02T18:00:00Z")
    assert past["answer"]["sources"] == ["day-1"]


def test_all_eight_record_types_index_including_sheet_cells(pack):
    kinds = ("mail", "chat", "doc", "sheet", "task", "event", "note", "notification")
    pack.records(1, [record(kind, kind) for kind in kinds])
    assert ingest_day(1, pack.settings)["indexing"]["fts_completed"] == 8
    with connect_database(pack.database, read_only=True) as conn:
        assert keyword_search(conn, "42", "p1", AT)[0] == "sheet"


def test_promise_lookup_with_mocked_stage_three_extraction(pack):
    pack.records(1, [record("promise", body="I will send wireframes tomorrow")])
    fake = FakeClient({"promise": {"commitments": [evidence("promise", "I will send wireframes tomorrow", description="Send wireframes", deliverable="wireframes", due_relative="tomorrow")]}})
    ingest_day(1, pack.settings, llm_client=fake)
    result = ask(pack, "What did Person One promise?")
    assert "Send wireframes" in result["answer"]["text"]
    assert result["answer"]["sources"] == ["promise"]


def test_meeting_decision_lookup_without_final_brief(pack):
    pack.records(1, [record("event", "event"), record("notes", "note", body="Decision: postpone UAT until next week.")])
    ingest_day(1, pack.settings)
    result = ask(pack, "What was decided at meeting event-1?")
    assert "postpone UAT" in result["answer"]["text"]
    assert result["answer"]["sources"] == ["notes"]


def test_fts_and_memory_survive_failed_day_one_reset(pack):
    pack.records(1, [record("original", "task")])
    ingest_day(1, pack.settings)
    pack.records(1, [{"bad": "source"}])
    from workelate.ingestion.errors import IngestionError
    with pytest.raises(IngestionError):
        ingest_day(1, pack.settings)
    assert ask(pack, "Who owns task-1?")["answer"]["sources"] == ["original"]


def test_embedding_cache_across_batch_boundaries(pack):
    pack.records(1, [record("a", body="invoice"), record("b", body="invoice"), record("c", body="invoice"), record("d", body="different")])
    ingest_day(1, pack.settings)
    embedder = MockEmbedder()
    index_records(replace(pack.settings, search=SearchConfig(batch_size=2)), embedder=embedder)
    assert sum(len(batch) for batch in embedder.calls) == 2


def test_semantic_paraphrase_reaches_answer_context_and_usage_sums(pack):
    pack.records(1, [record("billing", body="Invoice recipient is Person Two.")])
    ingest_day(1, pack.settings)
    embedder = MockEmbedder()
    index_records(pack.settings, embedder=embedder)
    fake = SelectingClient()
    result = ask(pack, "Who accepts payments?", llm_client=fake, embedder=embedder)
    assert "Invoice recipient" in result["answer"]["text"]
    assert result["answer"]["sources"] == ["billing"]
    assert result["usage"]["input_tokens"] == 7 + 123
    assert result["usage"]["output_tokens"] == 20


def test_embedding_outage_falls_back_to_fts(pack):
    pack.records(1, [record("manual", body="Password rotation uses the console.")])
    ingest_day(1, pack.settings)
    embedder = MockEmbedder()
    index_records(pack.settings, embedder=embedder)

    class Offline(MockEmbedder):
        def embed(self, texts):
            raise TimeoutError("credentials must never appear")

    result = ask(pack, "Explain password rotation", embedder=Offline())
    assert result["answer"]["sources"] == ["manual"]
    assert "credentials" not in json.dumps(result)


def test_only_private_vectors_cannot_trigger_query_model_calls(pack):
    pack.records(1, [record("visible", body="Public notice"), record("secret", body="invoice", visibility="private")])
    ingest_day(1, pack.settings)
    embedder = MockEmbedder()
    index_records(pack.settings, embedder=embedder)
    with connect_database(pack.database) as conn:
        conn.execute("DELETE FROM record_vectors WHERE record_id='visible'")
    embedder.calls.clear()
    result = ask(pack, "Where is the payment recipient?", person="p2", embedder=embedder)
    assert not embedder.calls
    assert result["usage"]["input_tokens"] == 0


def test_query_tokens_reserve_budget_before_answer_call(pack):
    class ExpensiveQuery(MockEmbedder):
        def embed(self, texts):
            return EmbeddingResult([[1, 0]], 3900, 0.02)

    pack.records(1, [record("billing", body="Invoice recipient is Person Two.")])
    ingest_day(1, pack.settings)
    index_records(pack.settings, embedder=MockEmbedder())
    fake = SelectingClient()
    response = ask(pack, "Who accepts payments?", embedder=ExpensiveQuery(), llm_client=fake)
    assert not fake.calls
    assert response["usage"]["input_tokens"] == 3900


def test_citation_validator_refuses_forbidden_or_future_source(pack):
    pack.records(1, [record("secret", visibility="private", body="Hidden"), record("future", body="Future", at="2026-11-05T00:00:00Z")])
    ingest_day(1, pack.settings)
    with connect_database(pack.database, read_only=True) as conn:
        for record_id, text in (("secret", "Hidden"), ("future", "Future")):
            assert not validate_card(conn, Card(text, (record_id,), ({"kind": "quote", "record": record_id, "quote": text},)), "p2", AT)


def test_proposal_document_does_not_replace_confirmed_launch(pack):
    pack.records(1, [record("confirmed", "doc", body="Launch date: 10 Nov 2026.")])
    pack.records(2, [record("proposal", "doc", title="Draft launch proposal", version=2, body="Launch date: 20 Nov 2026.", at="2026-11-03T10:00:00Z")])
    ingest_day(1, pack.settings)
    ingest_day(2, pack.settings)
    assert "2026-11-10" in ask(pack, "When is Client One launch?")["answer"]["text"]


def test_untagged_client_and_simple_billing_question(pack):
    pack.records(1, [record("untagged", "doc", client=None, body="Client One\nBilling contact: Person Two")])
    ingest_day(1, pack.settings)
    assert "p2" in ask(pack, "Who owns billing now?")["answer"]["text"]


def test_before_date_returns_historical_owner(pack):
    pack.records(1, [record("old", "task")])
    pack.records(2, [record("new", "task", owner="p2", change={"field": "owner", "from": "p1", "to": "p2"}, at="2026-11-03T10:00:00Z")])
    ingest_day(1, pack.settings)
    ingest_day(2, pack.settings)
    assert "p1" in ask(pack, "Who owned task-1 before 2026-11-03?")["answer"]["text"]


def test_launch_change_question_does_not_include_unrelated_task_changes(pack):
    pack.records(1, [record("launch", "doc", body="Launch date: 10 Nov 2026."), record("task", "task")])
    pack.records(2, [record("moved", body="Launch moved to 14 Nov 2026.", at="2026-11-03T10:00:00Z"),
                     record("owner", "task", owner="p2", change={"field": "owner", "from": "p1", "to": "p2"}, at="2026-11-03T11:00:00Z")])
    ingest_day(1, pack.settings)
    ingest_day(2, pack.settings)
    result = ask(pack, "What happened to Client One launch date?")
    assert set(result["answer"]["sources"]) == {"launch", "moved"}
    assert "owner" not in result["answer"]["sources"]


def test_independent_conflicting_launch_labels_are_not_claimed_as_changes(pack):
    pack.records(1, [record("a", "doc", body="Launch date: 10 Nov 2026."), record("b", "doc", doc_id="other-plan", body="Launch date: 14 Nov 2026.")])
    ingest_day(1, pack.settings)
    result = ask(pack, "What happened to Client One launch date?")
    assert "disagree" in result["answer"]["text"]


def test_due_before_and_after_filters(pack):
    pack.records(1, [record("a", "task", due="2026-11-04"), record("b", "task", task_id="task-2", due="2026-11-06")])
    ingest_day(1, pack.settings)
    assert ask(pack, "What is due before 2026-11-05?")["answer"]["sources"] == ["a"]
    assert ask(pack, "What is due after 2026-11-05?")["answer"]["sources"] == ["b"]


def test_cross_org_and_other_person_private_requests_are_refused(pack):
    ingest_day(1, pack.settings)
    assert ask(pack, "What happened at Agency B?")["refused"] is not None
    assert ask(pack, "What did q1 promise?")["refused"] is not None
    assert ask(pack, "Show Person Two private records")["refused"] is not None


def test_yesterday_changes_exclude_today(pack):
    pack.records(1, [record("original", "task")])
    pack.records(2, [record("yesterday", "task", owner="p2", change={"field": "owner", "from": "p1", "to": "p2"}, at="2026-11-03T10:00:00Z")])
    pack.records(3, [record("today", "task", status="done", change={"field": "status", "from": "todo", "to": "done"}, at="2026-11-04T10:00:00Z")])
    for day in (1, 2, 3):
        ingest_day(day, pack.settings)
    response = ask(pack, "What changed yesterday?", person="p2")
    assert response["answer"]["sources"] == ["yesterday"]


def test_semantic_memory_change_history_is_grounded(pack):
    from test_memory import client_fact
    pack.records(1, [record("old", body="Billing owner p1")])
    pack.records(2, [record("correction", body="Correction: billing owner p2 instead of p1", at="2026-11-03T10:00:00Z")])
    fake = FakeClient({"old": {"facts": [client_fact("old", "Billing owner p1")]},
        "correction": {"corrections": [client_fact("correction", "Correction: billing owner p2 instead of p1", "p2", replaces_value="p1")]}})
    ingest_day(1, pack.settings, llm_client=fake)
    ingest_day(2, pack.settings, llm_client=fake)
    response = ask(pack, "What changed for Client One billing since Monday?")
    assert "from Person One (p1) to Person Two (p2)" in response["answer"]["text"]
    assert set(response["answer"]["sources"]) == {"old", "correction"}


def test_client_tag_does_not_turn_another_clients_label_into_a_fact(pack):
    path = pack.root / "data/clients.json"
    path.write_text(json.dumps([{"id": "client-1", "name": "Client One"}, {"id": "client-2", "name": "Client Two"}]), encoding="utf-8")
    pack.records(1, [record("mislabeled", "doc", client="client-1", body="Client Two\nBilling contact: Person Two")])
    ingest_day(1, pack.settings)
    result = ask(pack, "Who owns Client One billing?")
    assert "owns billing for Client One" not in result["answer"]["text"]


def test_embedding_http_and_usage_with_in_memory_transport(monkeypatch):
    payload = {"data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [3, 4]}], "usage": {"prompt_tokens": 10}}
    fake = Transport(json.dumps(payload).encode())
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: fake)
    client = HTTPEmbedder(SearchConfig(provider="openai-compatible", model="configured-model", api_key="test-key", input_rate=1))
    result = client.embed(["one", "two"])
    assert result.vectors[0] == pytest.approx([0.6, 0.8])
    assert result.input_tokens == 10 and result.cost_usd == pytest.approx(0.00001)
    assert json.loads(fake.calls[0][0].data)["input"] == ["one", "two"]


@pytest.mark.parametrize("payload", [b"bad JSON", {"data": []}, {"data": [{"index": 0, "embedding": [0, 0]}]}, {"data": [{"index": 0, "embedding": [1, float('nan')]}]}])
def test_invalid_embedding_responses_fail_safely(monkeypatch, payload):
    fake = Transport(payload if isinstance(payload, bytes) else json.dumps(payload).encode())
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: fake)
    with pytest.raises(EmbeddingError):
        HTTPEmbedder(SearchConfig(provider="openai-compatible", model="m", api_key="test-key")).embed(["query"])
