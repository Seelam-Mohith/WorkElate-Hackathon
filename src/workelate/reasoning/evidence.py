"""Server-built, independently verifiable claim cards, never model-authored facts."""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime

from workelate.memory.repository import MemoryRepository
from workelate.memory.store import identity
from workelate.retrieval.entity_resolution import contains, tokens
from workelate.time_utils import normalized_utc, parse_calendar_date, parse_utc
from workelate.privacy.injection import instruction_text


@dataclass(frozen=True)
class Card:
    text: str
    sources: tuple[str, ...]
    proofs: tuple[dict, ...]
    entity_ids: tuple[str, ...] = ()
    direct: bool = True

    @property
    def id(self):
        return identity(self.text, self.sources)[:16]


def name(person_id, people):
    if person_id is None:
        return "Unknown owner"
    return people.get(person_id, {}).get("name", person_id) + " (" + person_id + ")"


def memory_card(current, entity, people):
    prefix = "Your correction: " if any(row["extraction_method"] == "feedback" for row in current.get("candidates", [])) else ""
    if current["status"] != "current":
        values = sorted({str(candidate["value"]) for candidate in current.get("candidates", [])})
        text = "The available evidence conflicts" + (": " + ", ".join(values) if values else ". Please specify the project.")
        return Card(prefix + text, tuple(current["source_ids"]), tuple({"kind": "memory", "fact_id": candidate["id"]} for candidate in current.get("candidates", [])), (entity.id,))
    predicate, value = current["predicate"], current["value"]
    if predicate in {"owner", "billing_owner"}:
        text = f"{name(value, people)} owns {'billing for ' if predicate == 'billing_owner' else ''}{entity.name}."
        ids = (entity.id, value)
    elif predicate in {"due_date", "launch_date"}:
        text = f"{entity.name} {'launch date is' if predicate == 'launch_date' else 'is due'} {value}."
        ids = (entity.id,)
    else:
        text = f"{entity.name}: {value.replace('_', ' ') if isinstance(value, str) else value}."
        ids = (entity.id,)
    return Card(prefix + text, tuple(current["source_ids"]), tuple({"kind": "memory", "fact_id": candidate["id"]} for candidate in current["candidates"]), ids)


def _date(value, at):
    value = value.strip().rstrip(".")
    try:
        return parse_calendar_date(value).isoformat()
    except ValueError:
        pass
    for fmt in ("%d %b %Y", "%d %B %Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError("Unsupported source date")


def business_observations(records, predicate, people):
    """Conservative explicit labels/confirmed changes; no prose summarization guesses."""
    rows = []
    for record in records:
        if instruction_text(record["body"]):
            continue
        if re.search(r"\b(draft|propos\w*|tentative)\b", record.get("title", record.get("subject", "")), re.I):
            continue
        if re.search(r"ignore.{0,50}instructions|system prompt|pending approval|not (?:yet )?approved|only a proposal", record["body"], re.I):
            continue
        for line in record["body"].splitlines():
            if re.search(r"\b(propos\w*|suggest\w*|maybe|could|might|draft)\b", line, re.I) or "?" in line:
                continue
            value, replacement = None, False
            if predicate == "billing_owner":
                match = re.search(r"\bbilling (?:owner|contact)\s*:\s*(.+)", line, re.I)
                replacement_match = re.search(r"(?:^|\bno[, ]+|\bcorrection[: ,]+)([^\n]{1,120}?)\s+(?:owns|handles)\s+billing\s+(?:now|instead)", line, re.I)
                takeover = re.search(r"\b([\w]+(?:\s+[\w]+)?)\s+takes?\s+over\s+(?:[\w -]+\s+)?billing\s+from\s+([\w ]+)", line, re.I)
                text = match[1] if match else replacement_match[1] if replacement_match else takeover[1] if takeover else None
                if text:
                    candidates = [p["id"] for p in people.values() if contains(text, p["id"]) or contains(text, p["name"])]
                    if not candidates:
                        firsts = [p for p in people.values() if contains(text, p["name"].split()[0])]
                        if len(firsts) == 1:
                            candidates = [firsts[0]["id"]]
                        elif takeover or replacement_match:
                            qualified = [p for p in firsts if "billing" in p.get("role", "").casefold()]
                            if len(qualified) == 1:
                                candidates = [qualified[0]["id"]]
                    if len(set(candidates)) == 1:
                        value = candidates[0]
                        replacement = replacement_match is not None or takeover is not None
            elif predicate == "launch_date":
                match = re.match(r"\s*launch date\s*:\s*(.+)", line, re.I)
                moved = re.search(r"\blaunch(?: date)?\s+(?:has been |is )?(?:moved|changed|rescheduled)\s+to\s+(.+)", line, re.I)
                raw = match[1] if match else moved[1] if moved else None
                if raw:
                    try:
                        value = _date(raw, record["at"])
                        replacement = moved is not None
                    except ValueError:
                        continue
            if value is not None:
                rows.append({"value": value, "record": record, "quote": line, "replacement": replacement})
    return sorted(rows, key=lambda row: (normalized_utc(row["record"]["at"]), row["record"]["id"]))


def scoped_business_records(records, client, directory):
    """Keep explicit client clauses when a record discusses multiple clients."""
    result = []
    other = [(key, value) for key, value in directory if key != client.id]
    for record in records:
        if not any(contains(record["body"], key) or contains(record["body"], value) for key, value in other):
            result.append(record)
            continue
        clauses = re.split(r"(?<=[.!?])\s+|\n", record["body"])
        supported = [line for line in clauses if (contains(line, client.id) or contains(line, client.name))
            and re.search(r"\b(billing|launch)\b", line, re.I)
            and not any(contains(line, key) or contains(line, value) for key, value in other)]
        if supported:
            result.append({**record, "body": "\n".join(supported)})
    return result


def _business_cards(entity, predicate, records, people, changes=False, since=None):
    observations = business_observations(records, predicate, people)
    # Within a document, the latest authorized version replaces that document's
    # earlier label. Independent sources retain uncertainty absent explicit change.
    active = []
    for observation in observations:
        record = observation["record"]
        if observation["replacement"]:
            active = []
        elif any(item["replacement"] for item in active):
            continue
        elif record["type"] == "doc":
            active = [old for old in active if old["record"].get("doc_id") != record.get("doc_id")]
        active.append(observation)
    if not active:
        return []
    if changes and since and parse_utc(active[-1]["record"]["at"]) < since:
        return []
    if changes and len({row["value"] for row in active}) == 1 and len(observations) > 1 and observations[0]["value"] != active[-1]["value"]:
        chosen = [observations[0], active[-1]]
        values = [name(row["value"], people) if predicate == "billing_owner" else row["value"] for row in chosen]
        text = f"{entity.name} {predicate.replace('_', ' ')} changed from {values[0]} to {values[1]}."
    else:
        chosen = active
        values = {row["value"] for row in active}
        if len(values) > 1:
            text = f"The available sources disagree about {entity.name} {predicate.replace('_', ' ')}."
        elif predicate == "billing_owner":
            text = f"{name(active[-1]['value'], people)} owns billing for {entity.name}."
        else:
            text = f"{entity.name} launch date is {active[-1]['value']}."
    # A request is not confirmation on its own. Once a later explicit document
    # confirms the same date, retain the initiating mail as corroborating context.
    if predicate == "launch_date" and len({row["value"] for row in active}) == 1:
        confirmed = active[-1]
        for record in records:
            if record["type"] != "mail" or parse_utc(record["at"]) > parse_utc(confirmed["record"]["at"]) or instruction_text(record["body"]):
                continue
            for line in re.split(r"(?<=[.!?])\s+|\n", record["body"]):
                request = re.search(r"\b(?:need to|please) move the launch to (\d{1,2}\s+[A-Za-z]+(?:\s+\d{4})?|\d{4}-\d{2}-\d{2})\b", line, re.I)
                if not request or "?" in line or re.search(r"\b(?:maybe|proposed|draft|not)\b", line, re.I):
                    continue
                try:
                    agrees = _date(request[1], record["at"]) == confirmed["value"]
                except ValueError:
                    try:
                        agrees = datetime.strptime(request[1], "%d %b").strftime("%m-%d") == confirmed["value"][5:]
                    except ValueError:
                        agrees = False
                if agrees:
                    chosen.append({"record": record, "quote": line})
    return [Card(text, tuple(sorted({row["record"]["id"] for row in chosen})),
        tuple({"kind": "quote", "record": row["record"]["id"], "quote": row["quote"]} for row in chosen),
        (entity.id, *([active[-1]["value"]] if predicate == "billing_owner" and len({r["value"] for r in active}) == 1 else [])))]


def memory_change_card(memory, state, entity, predicate, people, person_id, times):
    if state["status"] != "current":
        return [memory_card(state, entity, people)]
    history = memory.get_fact_history(entity.id, predicate, person_id, as_of=times.as_of, subject_type=entity.kind)
    before = [item for item in history if times.since is None or parse_utc(item["effective_at"]) < times.since]
    old = before[-1] if times.since and before else history[0] if history else None
    latest = max((parse_utc(item["effective_at"]) for item in state["candidates"]), default=times.as_of)
    if old is None or old["value"] == state["value"] or times.since and latest < times.since:
        return []
    old_value = name(old["value"], people) if predicate == "billing_owner" else str(old["value"])
    new_value = name(state["value"], people) if predicate == "billing_owner" else str(state["value"])
    return [Card(f"{entity.name} {predicate.replace('_', ' ')} changed from {old_value} to {new_value}.",
        tuple(sorted(set(old["source_ids"]) | set(state["source_ids"]))),
        tuple({"kind": "memory", "fact_id": row["id"]} for row in [old, *state["candidates"]]), (entity.id,))]


def direct_cards(conn, question, classification, time_query, resolution, person_id, *, through_day=None):
    memory = MemoryRepository(conn, through_day=through_day)
    cards = []
    sources = [r for r in resolution.records if parse_utc(r["at"]) <= time_query.as_of and not instruction_text(r["body"])]
    clients = {entity.id for entity in resolution.entities if entity.kind == "client"}
    tasks = [entity for entity in resolution.entities if entity.kind == "task"]
    meetings = [entity for entity in resolution.entities if entity.kind == "meeting"]
    if classification.category == "decisions":
        for source in sources:
            if meetings and source.get("event_id") not in {entity.id for entity in meetings}:
                continue
            if clients and source.get("client") not in clients:
                continue
            for line in source["body"].splitlines():
                if re.match(r"\s*Decision:\s*\S", line) and not re.search(r"\b(proposed|draft|tentative)\b", line, re.I):
                    cards.append(Card(line.strip(), (source["id"],), ({"kind": "quote", "record": source["id"], "quote": line},)))
        return cards[:8]
    if classification.category == "multi_hop" and "before" in question.casefold() and tasks and meetings:
        task, meeting = tasks[0], memory.get_meeting(meetings[0].id, person_id, as_of=time_query.cutoff)
        if meeting:
            bound = min(time_query.cutoff, parse_utc(meeting["start_at"]))
            state = memory.get_current_fact(task.id, "status", person_id, as_of=bound)
            if state:
                completed = state["status"] == "current" and state["value"] == "done"
                text = f"{task.name} {'was completed' if completed else 'has no confirmed completion recorded'} before the scheduled meeting."
                cards.append(Card(text, tuple(sorted(set(state["source_ids"]) | {meeting["record_id"]})),
                    tuple([{"kind": "memory", "fact_id": row["id"]} for row in state.get("candidates", [])] +
                          [{"kind": "field", "record": meeting["record_id"], "path": "event_id", "value": meetings[0].id}]), (task.id, meetings[0].id)))
        return cards
    if classification.category == "commitments" or time_query.due_date:
        owners = {entity.id for entity in resolution.entities if entity.kind == "person"}
        for item in memory.get_commitments(person_id, as_of=time_query.as_of):
            if clients and item["client_id"] not in clients or owners and item["owner_id"] not in owners:
                continue
            if time_query.due_date:
                due = item["due_date"]
                matches = due is not None and {"on": due == time_query.due_date, "before": due < time_query.due_date,
                    "after": due > time_query.due_date, "since": due >= time_query.due_date}[time_query.due_relation]
                if not matches or item["status"] in {"completed", "cancelled"}:
                    continue
            if classification.category == "commitments" and item["kind"] != "promise" and "task" not in question.casefold():
                continue
            text = f"{name(item['owner_id'], resolution.people)}: {item['description']} — {item['status']}" + (f", due {item['due_date']}" if item["due_date"] else "; no due date recorded") + "."
            if item.get("corrected_fields"):
                text = "Your correction: " + text
            cards.append(Card(text, tuple(item["source_ids"]), ({"kind": "commitment", "commitment_id": item["id"], "status": item["status"]},), (item["owner_id"],)))
        return cards[:8]
    if classification.category == "changes":
        for source in sources:
            if classification.predicate in {"billing_owner", "launch_date"}:
                continue
            if source["type"] != "task" or source["change"]["field"] not in {"owner", "due", "status"}:
                continue
            if time_query.since and parse_utc(source["at"]) < time_query.since:
                continue
            if clients and source.get("client") not in clients or tasks and source["task_id"] not in {task.id for task in tasks}:
                continue
            field_filter = "owner" if re.search(r"\b(owner|ownership)\b", question, re.I) else "due" if re.search(r"\b(due|deadline)\b", question, re.I) else None
            if field_filter and source["change"]["field"] != field_filter:
                continue
            change = source["change"]
            old, new = change["from"], change["to"]
            if change["field"] == "owner":
                old, new = name(old, resolution.people), name(new, resolution.people)
            cards.append(Card(f"{source['title']} {change['field']} changed from {old} to {new}.", (source["id"],),
                ({"kind": "field", "record": source["id"], "path": "change", "value": change},), (source["task_id"],)))
    else:
        for task in tasks:
            predicates = ("owner", "due_date") if classification.predicate == "owner_due" else (classification.predicate,)
            for predicate in predicates:
                if predicate in {"owner", "due_date", "status"}:
                    state = memory.get_current_fact(task.id, predicate, person_id, as_of=time_query.as_of, subject_type="task")
                    if state:
                        cards.append(memory_card(state, task, resolution.people))
    if classification.predicate in {"billing_owner", "launch_date"}:
        for client in [entity for entity in resolution.entities if entity.kind == "client"]:
            relevant = [source for source in sources if source.get("client") == client.id or source.get("client") is None and (contains(source["body"], client.name) or contains(source["body"], client.id))]
            directory = list(conn.execute("SELECT id,name FROM clients"))
            # A client tag is a hint, not proof. Generic labels in a multi-client
            # document, or text explicitly naming another client, are ambiguous.
            relevant = scoped_business_records(relevant, client, directory)
            state = memory.get_current_fact(client.id, classification.predicate, person_id, as_of=time_query.as_of, subject_type="client")
            if state and classification.category == "changes":
                cards.extend(memory_change_card(memory, state, client, classification.predicate, resolution.people, person_id, time_query))
            else:
                cards.extend([memory_card(state, client, resolution.people)] if state else _business_cards(client, classification.predicate, relevant, resolution.people, classification.category == "changes", time_query.since))
    if classification.category == "meeting":
        for entity in meetings:
            meeting = memory.get_meeting(entity.id, person_id, as_of=time_query.as_of)
            if meeting:
                attendee_names = [name(p, resolution.people) if p in resolution.people else p for p in meeting["attendees"]]
                cards.append(Card(f"{meeting['title']} starts at {meeting['start_at']}; attendees: {', '.join(attendee_names)}.",
                    (meeting["record_id"],), ({"kind": "field", "record": meeting["record_id"], "path": "attendees", "value": meeting["attendees"]},), (entity.id,)))
    return list({card.id: card for card in cards}.values())[:8]


def quote_cards(records, question, *, semantic_ids=None):
    result = []
    for record in records:
        if instruction_text(record["body"]):
            continue
        body = record["body"]
        chunks = [match for match in re.finditer(r"[^\n.!?]+[.!?]?", body) if match.group().strip()]
        if not chunks:
            continue
        index = max(range(len(chunks)), key=lambda i: len(tokens(chunks[i].group()) & tokens(question)))
        selected = body[chunks[max(0, index - 1)].start():chunks[min(len(chunks) - 1, index + 1)].end()]
        if not tokens(selected) & tokens(question) and record["id"] not in (semantic_ids or set()):
            continue
        if len(selected) > 1000:
            continue  # Do not drop qualifiers or negations to squeeze a long sentence.
        result.append(Card("Source at " + record["at"] + " says: “" + selected.strip() + "”", (record["id"],),
            ({"kind": "quote", "record": record["id"], "quote": selected},), direct=False))
    return result
