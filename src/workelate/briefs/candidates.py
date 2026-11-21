"""Build grounded brief candidates from authorized memory and explicit changes."""

import re
from dataclasses import dataclass, replace
from datetime import timedelta

from workelate.feedback.repository import verdict_score
from workelate.memory.repository import MemoryRepository
from workelate.reasoning.evidence import Card, _business_cards, business_observations, memory_card, name
from workelate.retrieval.entity_resolution import Entity, STOP, contains
from workelate.time_utils import parse_utc, utc_to_local_date
from workelate.privacy.injection import instruction_text


@dataclass(frozen=True)
class Candidate:
    card: Card
    category: str
    priority: int
    new: bool
    owner: str | None = None
    what: str | None = None
    due: str | None = None
    question: str | None = None


def client_records(records, client_id, client_name, meeting_id):
    if client_id is None:
        return [record for record in records if record.get("event_id") == meeting_id or
                contains(record["body"], meeting_id) or len(client_name) > 4 and contains(record["body"], client_name)]
    aliases = {client_id, client_name}
    prefixes = {}
    for record in records:
        match = re.match(r"t-([a-zA-Z]{2,5})-", record.get("task_id", ""))
        if match and record.get("client"):
            prefixes.setdefault(match[1].casefold(), set()).add(record["client"])
    aliases.update(alias for alias, ids in prefixes.items() if ids == {client_id} and alias not in STOP)
    return [record for record in records if record.get("client") == client_id or
            record.get("event_id") == meeting_id or record.get("client") is None and client_id is not None and
            any(contains(record["body"], alias) or contains(record.get("title", record.get("subject", "")), alias) for alias in aliases if alias)]


def build_candidates(conn, person_id, meeting, records, previous, cutoff, through_day, preferences):
    memory = MemoryRepository(conn, through_day=through_day)
    person = memory._sources.get_person(person_id)
    people = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM people WHERE org_id=?", (person["org_id"],))}
    client_id = meeting.get("client")
    row = conn.execute("SELECT name FROM clients WHERE id=?", (client_id,)).fetchone()
    client_name = row[0] if row else meeting["title"]
    relevant = client_records(records, client_id, client_name, meeting["event_id"])
    relevant_ids = {record["id"] for record in relevant}
    by_id = {record["id"]: record for record in records}
    window = parse_utc(previous["start"]) if previous else None
    is_new = lambda at: window is None or parse_utc(at) > window
    def fresh_observation(item):
        if is_new(item["observed_at"]):
            return True
        if previous and item["extraction_method"] == "feedback":
            from workelate.feedback.service import DAY_ONE
            previous_preparation = (utc_to_local_date(previous["start"], person["timezone"]) - DAY_ONE).days
            link = conn.execute("SELECT available_day FROM personal_fact_links WHERE fact_id=? AND person_id=?", (item["id"], person_id)).fetchone()
            return link is not None and link[0] > previous_preparation
        return False
    candidates = []
    task_ids = {record["task_id"] for record in relevant if record["type"] == "task"}
    for task_id in sorted(task_ids):
        task_records = [record for record in relevant if record.get("task_id") == task_id]
        latest = task_records[-1]
        entity = Entity(task_id, "task", latest["title"], [], client_id)
        for field, predicate in (("owner", "owner"), ("due", "due_date"), ("status", "status")):
            state = memory.get_current_fact(task_id, predicate, person_id, as_of=cutoff, subject_type="task")
            if state is None:
                continue
            feedback = [item for item in state.get("candidates", []) if item["extraction_method"] == "feedback"]
            if feedback and any(fresh_observation(item) for item in feedback):
                candidates.append(Candidate(memory_card(state, entity, people), "correction", 105, True))
                continue
            changes = [record for record in task_records if is_new(record["at"]) and record["change"]["field"] == field
                       and state["status"] == "current" and state["value"] == record[field] and record["id"] in state["source_ids"]]
            if changes:
                source = changes[-1]
                change = source["change"]
                old = name(change["from"], people) if field == "owner" else change["from"]
                new = name(change["to"], people) if field == "owner" else change["to"]
                text = f"{source['title']} {field} changed from {old} to {new}."
                priority = 100 if field == "status" and state["value"] == "blocked" else 85 if field == "due" else 80
                proof = ({"kind": "field", "record": source["id"], "path": "change", "value": change},)
                question = f"What is needed to unblock {source['title']}?" if priority == 100 else f"Can we confirm the new deadline for {source['title']}?" if field == "due" else None
                candidates.append(Candidate(Card(text, (source["id"],), proof, (task_id,)), "blocker" if priority == 100 else "task", priority, True, source["owner"], question=question))
            elif field == "status" and state["status"] == "current" and state["value"] == "blocked":
                candidates.append(Candidate(memory_card(state, entity, people), "blocker", 100, False, latest["owner"], question=f"What is needed to unblock {latest['title']}?"))
            elif field == "status" and state["status"] == "current" and state["value"] == "done":
                # Historical completion can provide context when there are fresh
                # updates, but cannot alone turn nothing_new into a filler brief.
                candidates.append(Candidate(memory_card(state, entity, people), "context", 60, False, latest["owner"]))
        if previous:
            created = [record for record in task_records if record["change"]["field"] == "created" and is_new(record["at"])]
            if created:
                source = created[-1]
                candidates.append(Candidate(Card(f"New task: {source['title']}.", (source["id"],),
                    ({"kind": "field", "record": source["id"], "path": "change", "value": source["change"]},), (task_id,)), "task", 65, True, source["owner"]))
    for record in relevant:
        if instruction_text(record["body"]):
            continue
        if not is_new(record["at"]):
            continue
        if record["type"] == "note":
            for line in record["body"].splitlines():
                if re.match(r"\s*Decision:\s*\S", line) and not re.search(r"\b(proposed|draft|tentative)\b", line, re.I):
                    candidates.append(Candidate(Card(line.strip(), (record["id"],), ({"kind": "quote", "record": record["id"], "quote": line},)),
                        "decision", 90, True, question="What are the next steps for the recorded decision?"))
    for kind, subject_field in (("doc", "doc_id"), ("sheet", "sheet_id")):
        for entity_id in sorted({record[subject_field] for record in relevant if record["type"] == kind}):
            for change in memory.get_artifact_changes(kind, entity_id, person_id, as_of=cutoff):
                if not is_new(change["effective_at"]):
                    continue
                old, new = [by_id[source] for source in change["source_ids"]]
                if kind == "doc":
                    # Semantic business deltas are handled below. A changed document
                    # version alone is not an approved decision.
                    continue
                text = f"{new['name']} {change['field']} changed from {change['previous_value']} to {change['new_value']}."
                proofs = tuple({"kind": "field", "record": source["id"], "path": "cells", "value": source["cells"]} for source in (old, new))
                candidates.append(Candidate(Card(text, tuple(change["source_ids"]), proofs), "sheet", 55, True))
    if client_id:
        entity = Entity(client_id, "client", client_name, [], client_id)
        for predicate in ("billing_owner", "launch_date"):
            state = memory.get_current_fact(client_id, predicate, person_id, as_of=cutoff, subject_type="client")
            if state and any(fresh_observation(item) for item in state.get("candidates", [])):
                candidates.append(Candidate(memory_card(state, entity, people), "correction" if any(item["extraction_method"] == "feedback" for item in state.get("candidates", [])) else "fact", 105, True))
            elif state is None:
                observations = business_observations(relevant, predicate, people)
                if len({str(item["value"]) for item in observations}) > 1:
                    changed_cards = _business_cards(entity, predicate, relevant, people, changes=True, since=window)
                    for card in changed_cards:
                        candidates.append(Candidate(card, "fact", 85, True))
                    if not changed_cards:
                        for card in _business_cards(entity, predicate, relevant, people):
                            candidates.append(Candidate(card, "context", 85, False))
    for item in memory.get_commitments(person_id, as_of=cutoff):
        if client_id is None and not set(item["source_ids"]) & relevant_ids:
            continue
        if item["client_id"] != client_id and not (item["client_id"] is None and set(item["source_ids"]) & relevant_ids):
            continue
        proofs = ({"kind": "commitment", "commitment_id": item["id"], "status": item["status"]},)
        sources = tuple(item["source_ids"])
        new = is_new(item["created_at"]) or is_new(item["updated_at"])
        if item["kind"] == "promise":
            if item["status"] in {"completed", "cancelled"}:
                if new:
                    candidates.append(Candidate(Card(f"{item['description']} — {item['status']}.", sources, proofs), "delivery", 80, True, item["owner_id"]))
                continue
            text = f"{name(item['owner_id'], people)} promised {item['description']}" + (f", due {item['due_date']}." if item["due_date"] else "; no due date is recorded.")
            candidates.append(Candidate(Card(text, sources, proofs), "promise" if item["due_date"] else "commitment", 70, new,
                item["owner_id"], item["description"], item["due_date"], f"Is {item['description']} still on track" + (f" for {item['due_date']}?" if item["due_date"] else "?")))
        elif item["entity_id"] in task_ids and item["status"] not in {"completed", "cancelled", "unknown"} and item["due_date"]:
            from workelate.config import get_timezone
            local_date = parse_utc(meeting["start"]).astimezone(get_timezone(person["timezone"])).date()
            previous_date = window.astimezone(get_timezone(person["timezone"])).date() if window else None
            due = item["due_date"]
            if due <= (local_date + timedelta(days=1)).isoformat() and (previous_date is None or due > previous_date.isoformat()):
                prefix = "Your correction: " if item.get("corrected_fields") else ""
                candidates.append(Candidate(Card(prefix + f"{item['description']} is {item['status']}, due {due}.", sources, proofs), "deadline", 75, True,
                    item["owner_id"], question=f"Can we confirm the plan for {item['description']} before its deadline?"))
    # A verdict changes relevance, not source truth or access rights.
    scored = []
    for candidate in {candidate.card.id: candidate for candidate in candidates}.values():
        if preferences["skip_newsletters"] and any(re.search(r"newsletter|news@|digest", by_id[source].get("subject", "") + by_id[source]["author"], re.I) for source in candidate.card.sources):
            continue
        priority = candidate.priority + 8 * verdict_score(conn, person_id, client_id, candidate.card.text, cutoff, through_day=through_day)
        if candidate.category == "blocker" and preferences["blockers_first"]:
            priority += 100
        if candidate.owner == person_id:
            priority += 20 if preferences["my_tasks_first"] else 3
        scored.append((priority, replace(candidate, priority=priority)))
    return [candidate for _, candidate in sorted(scored, key=lambda pair: (-pair[0], pair[1].card.id))], relevant
