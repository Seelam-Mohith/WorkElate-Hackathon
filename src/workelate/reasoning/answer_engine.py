"""End-to-end ask: authorized retrieval, deterministic reasoning, constrained LLM.

Model claims must select verified server-built cards. Arbitrary prose with plausible
citations is never accepted as grounded merely because the IDs exist.
"""

import json
import math
import re
import time

from workelate.ingestion.loader import parse_json
from workelate.reasoning.citation_validator import validate_card, validate_generated
from workelate.reasoning.classifier import classify
from workelate.reasoning.evidence import direct_cards, quote_cards
from workelate.reasoning.llm_client import LLMError, client_from_config
from workelate.retrieval.context_builder import build_context, token_bound
from workelate.retrieval.embeddings import embedding_client, unit_vector
from workelate.retrieval.entity_resolution import resolve
from workelate.retrieval.entity_resolution import contains
from workelate.retrieval.hybrid import retrieve
from workelate.retrieval.temporal import interpret_time
from workelate.schemas.answers import AskResponse, GeneratedAnswer
from workelate.storage.database import connect_database
from workelate.storage.repository import Repository
from workelate.time_utils import normalized_utc, parse_utc


class Meter:
    def __init__(self):
        self.input_tokens = self.output_tokens = 0
        self.cost = 0.0
        self.basis = "no_model_calls"

    def add(self, input_tokens, output_tokens=0, cost=None, *, estimated=False):
        if any(type(value) is not int or value < 0 for value in (input_tokens, output_tokens)):
            raise ValueError("Invalid model usage")
        if cost is not None and (type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0):
            cost = None
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cost = None if self.cost is None or cost is None else self.cost + cost
        self.basis = "estimated_utf8_bound" if estimated or self.basis == "estimated_utf8_bound" else "provider_reported"


def ask_question(person_id, at, question, settings, *, llm_client=None, embedder=None):
    from workelate.ingestion.service import database_path

    started = time.monotonic()
    cutoff = parse_utc(at)
    meter = Meter()
    matched = []

    def result(*, text=None, sources=None, clarify=None, refused=None):
        payload = {"answer": {"text": text, "sources": sources or []} if text is not None else None,
                   "matched": matched, "clarify": clarify, "refused": {"reason": refused} if refused else None,
                   "usage": {"input_tokens": meter.input_tokens, "output_tokens": meter.output_tokens,
                             "cost_usd": meter.cost, "ms": int((time.monotonic() - started) * 1000)}}
        return AskResponse.model_validate(payload).model_dump()

    path = database_path(settings)
    if not path.exists():
        return result(text="I don't have any ingested evidence to answer that.")
    with connect_database(path) as conn:
        repository = Repository(conn)
        person = repository.get_person(person_id)
        if person is None:
            return result(refused="I can only answer for an identified person using records they may access.")
        from workelate.feedback.service import DAY_ONE
        from workelate.time_utils import utc_to_local_date
        through_day = (utc_to_local_date(cutoff, person["timezone"]) - DAY_ONE).days + 1
        repository = Repository(conn, through_day=through_day)

        def finish(payload, outcome):
            conn.execute("""INSERT INTO ask_runs
                (person_id,cutoff,input_tokens,output_tokens,cost_usd,token_basis,ms,outcome) VALUES (?,?,?,?,?,?,?,?)""",
                (person_id, normalized_utc(cutoff), meter.input_tokens, meter.output_tokens, meter.cost,
                 meter.basis, payload["usage"]["ms"], outcome))
            return payload

        foreign_org = any(contains(question, row["id"]) or contains(question, row["name"])
            for row in conn.execute("SELECT id,name FROM organizations WHERE id<>?", (person["org_id"],)))
        foreign_person = any(contains(question, row[0]) for row in conn.execute("SELECT id FROM people WHERE org_id<>?", (person["org_id"],)))
        if foreign_org or foreign_person:
            return finish(result(refused="I can only answer from records in your organization that you may access."), "outside_organization")
        if len(question.encode("utf-8")) + 512 > 4000:
            return finish(result(clarify={"question": "Please shorten the question so its evidence can fit within the input limit.", "options": []}), "question_too_large")
        try:
            times = interpret_time(question, cutoff, person["timezone"])
        except ValueError:
            return finish(result(clarify={"question": "Which valid calendar date do you mean?", "options": []}), "invalid_question_date")
        through_day = min(through_day, (utc_to_local_date(times.as_of, person["timezone"]) - DAY_ONE).days + 1)
        classification = classify(question)
        resolution = resolve(conn, person_id, question, times.as_of, through_day=through_day)
        matched = resolution.matched
        if resolution.ambiguous:
            return finish(result(clarify={"question": "Which person or work item do you mean?",
                "options": [{"id": item.id, "text": item.name} for item in resolution.ambiguous[:8]]}), "ambiguous_entity")
        if "private" in question.casefold() and any(entity.kind == "person" and entity.id != person_id for entity in resolution.entities):
            return finish(result(refused="Another person's private records are not available to you."), "private_request")
        if re.search(r"\b(?:say|said|written|write)\b.{0,30}\babout me\b", question, re.I):
            speakers = {entity.id for entity in resolution.entities if entity.kind == "person" and entity.id != person_id}
            resolution.records = [record for record in resolution.records if record["author"] in speakers and
                (contains(record["body"], person_id) or contains(record["body"], person["name"]))]
            if not resolution.records:
                return finish(result(refused="I don't have authorized statements by that person about you."), "no_authorized_statement")
        if re.search(r"\b(?:thing|one)\b", question, re.I) and classification.category == "status" and not any(entity.kind == "task" for entity in resolution.entities):
            from workelate.memory.repository import MemoryRepository
            tasks = [item for item in MemoryRepository(conn, through_day=through_day).get_commitments(person_id, as_of=cutoff) if item["kind"] == "task"]
            weekdays = [i for i, name in enumerate(("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")) if contains(question, name)]
            if weekdays:
                from workelate.time_utils import parse_calendar_date
                tasks = [item for item in tasks if item["due_date"] and parse_calendar_date(item["due_date"]).weekday() in weekdays]
            return finish(result(clarify={"question": "Which work item do you mean?", "options": [{"id": item["entity_id"], "text": item["description"]} for item in tasks[:8]]}), "vague_work_item")
        cards = [card for card in direct_cards(conn, question, classification, times, resolution, person_id, through_day=through_day)
                 if validate_card(conn, card, person_id, cutoff, through_day=through_day)]
        if not cards and classification.category == "changes" and times.since:
            return finish(result(text="I don't have confirmed recorded changes in that time window."), "no_recorded_changes")
        if not cards and classification.predicate in {"billing_owner", "launch_date"} and not any(entity.kind == "client" for entity in resolution.entities):
            clients = {}
            for record in resolution.records:
                if record.get("client"):
                    row = conn.execute("SELECT name FROM clients WHERE id=?", (record["client"],)).fetchone()
                    clients[record["client"]] = row[0]
            if len(clients) > 1:
                return finish(result(clarify={"question": "Which client do you mean?", "options": [{"id": key, "text": value} for key, value in sorted(clients.items())]}), "ambiguous_client")
        query_vector, model_key = None, None
        if not cards:
            embedder = embedder or embedding_client(settings.search, cache_dir=settings.runtime_dir / "models")
            if embedder and conn.execute("SELECT 1 FROM record_vectors WHERE model_key=? AND record_id IN (SELECT value FROM json_each(?)) LIMIT 1", (embedder.key, json.dumps([record["id"] for record in resolution.records if parse_utc(record["at"]) <= times.as_of]))).fetchone():
                tick = time.monotonic()
                embedded = None
                try:
                    embedded = embedder.embed([question])
                    meter.add(embedded.input_tokens if type(embedded.input_tokens) is int and embedded.input_tokens >= 0 else len(question.encode("utf-8")) + 32,
                              cost=embedded.cost_usd, estimated=embedded.input_tokens is None)
                    if len(embedded.vectors) != 1:
                        raise ValueError("Invalid query embedding")
                    query_vector = unit_vector(embedded.vectors[0])
                    model_key = embedder.key
                except Exception:
                    query_vector = None
                    # No response means unknown provider usage; conservative input estimate.
                    if embedded is None:
                        meter.add(len(question.encode("utf-8")) + 32, cost=None, estimated=True)
                conn.execute("INSERT INTO search_usage (phase,model,input_tokens,cost_usd,latency_ms,outcome) VALUES ('query',?,?,?,?,?)",
                    (embedder.model, embedded.input_tokens if embedded else None, embedded.cost_usd if embedded else None,
                     int((time.monotonic() - tick) * 1000), "completed" if query_vector is not None else "failed"))
        retrieved = retrieve(conn, question, person_id, times, resolution, classification, depth=settings.search.depth,
                             query_vector=query_vector, model_key=model_key, through_day=through_day, settings=settings)
        if cards:
            # Add answer-bearing people/task IDs, without inventing record citations.
            for card in cards:
                for entity_id in card.entity_ids:
                    if not any(item["id"] == entity_id for item in matched):
                        label = resolution.people.get(entity_id, {}).get("name", entity_id)
                        matched.append({"phrase": label, "id": entity_id, "sources": list(card.sources)})
            return finish(result(text=" ".join(card.text for card in cards), sources=sorted({source for card in cards for source in card.sources})), "deterministic")
        cards = [card for card in quote_cards(retrieved.records, question, semantic_ids=retrieved.semantic_ids) if validate_card(conn, card, person_id, cutoff, through_day=through_day)]
        if not cards:
            return finish(result(text="I don't have enough authorized evidence to answer that."), "insufficient_evidence")
        client = llm_client or client_from_config(settings.llm)
        selected, outcome = cards[:1], "extractive_fallback"
        messages, bounded_cards = build_context(question, normalized_utc(times.as_of), cards, budget=4000 - meter.input_tokens)
        if client is not None and messages and bounded_cards:
            response = None
            try:
                response = client.complete(messages)
                inputs = response.input_tokens if response.input_tokens is not None else token_bound(messages)
                outputs = response.output_tokens if response.output_tokens is not None else len(response.content.encode("utf-8"))
                meter.add(inputs, outputs, response.cost_usd, estimated=response.input_tokens is None or response.output_tokens is None)
                if meter.input_tokens > 4000:
                    raise ValueError("Provider reported over-budget usage")
                generated = GeneratedAnswer.model_validate(parse_json(response.content))
                selected = validate_generated(generated, bounded_cards, conn, person_id, cutoff, through_day=through_day)
                outcome = "llm_verified"
            except Exception as exc:
                if response is None and getattr(exc, "code", "") != "provider_not_configured":
                    meter.add(token_bound(messages), cost=None, estimated=True)
                selected, outcome = [], "unverified_model_response"
        if not selected:
            return finish(result(text="I couldn't verify an answer from the available evidence."), outcome)
        return finish(result(text=" ".join(card.text for card in selected), sources=sorted({source for card in selected for source in card.sources})), outcome)
