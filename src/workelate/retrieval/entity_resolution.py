"""Stable identities from the user's authorized source view, never a global index."""

import json
import re
from collections import defaultdict
from dataclasses import dataclass

STOP = set("who what when where why how is are was were the a an for of to in on by now it my me this that did do does owned owns owner due date ready status task account please and changed changes since before after happened".split())


def tokens(text):
    words = set(re.findall(r"[\w]+", text.casefold())) - STOP
    return {word[:-1] if len(word) > 3 and word.endswith("s") else word for word in words} - STOP


def contains(text, phrase):
    return re.search(r"(?<!\w)" + re.escape(phrase.casefold()) + r"(?!\w)", text.casefold()) is not None


@dataclass
class Entity:
    id: str
    kind: str
    name: str
    sources: list[str]
    client_id: str | None = None


@dataclass
class Resolution:
    entities: list[Entity]
    ambiguous: list[Entity]
    records: list[dict]
    people: dict

    @property
    def matched(self):
        return [{"phrase": item.name, "id": item.id, "sources": item.sources[:3]} for item in self.entities]


def resolve(conn, person_id, question, cutoff, *, through_day=None) -> Resolution:
    from workelate.storage.repository import Repository
    from workelate.time_utils import normalized_utc

    cutoff = normalized_utc(cutoff)
    repository = Repository(conn, through_day=through_day)
    person = repository.get_person(person_id)
    if person is None:
        return Resolution([], [], [], {})
    from workelate.privacy.injection import instruction_text
    records = [record for record in repository.get_accessible_records(person_id, until=cutoff) if not instruction_text(record["body"])]
    people = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM people WHERE org_id=?", (person["org_id"],))}
    catalog, aliases, cities = {}, defaultdict(set), defaultdict(set)
    directory = {row["id"]: row["name"] for row in conn.execute("SELECT * FROM clients")}
    for source in records:
        client = source.get("client")
        if client:
            catalog.setdefault(("client", client), Entity(client, "client", directory.get(client, client), [], client)).sources.append(source["id"])
            for phrase in (client, directory.get(client, client), directory.get(client, client).split()[0]):
                aliases[phrase.casefold()].add(client)
            match = re.match(r"t-([a-zA-Z]{2,5})-", source.get("task_id", ""))
            if match and match[1].casefold() not in STOP:
                aliases[match[1].casefold()].add(client)
            for participant in [source["author"], *source.get("attendees", [])]:
                if participant in people:
                    city = people[participant]["timezone"].split("/")[-1].replace("_", " ")
                    cities[(city + " account").casefold()].add(client)
        for key, kind, label in (("task_id", "task", "title"), ("doc_id", "document", "title"), ("sheet_id", "sheet", "name"), ("event_id", "meeting", "title")):
            if key in source and (key != "event_id" or source["type"] == "event"):
                entity = catalog.setdefault((kind, source[key]), Entity(source[key], kind, source.get(label, source[key]), [], client))
                entity.sources.append(source["id"])
        if client is None:
            for client_id, client_name in directory.items():
                if contains(source["body"], client_id) or contains(source["body"], client_name):
                    catalog.setdefault(("client", client_id), Entity(client_id, "client", client_name, [], client_id)).sources.append(source["id"])
                    for phrase in (client_id, client_name, client_name.split()[0]):
                        aliases[phrase.casefold()].add(client_id)
    # Explicit semantic aliases only, requiring every supporting record in this view.
    allowed = {source["id"] for source in records}
    for row in conn.execute("""SELECT f.subject_id,f.value_json FROM knowledge_facts f
        WHERE f.org_id=? AND f.predicate LIKE 'alias:%' AND f.observed_at<=?
        AND EXISTS (SELECT 1 FROM fact_sources s WHERE s.fact_id=f.id)
        AND NOT EXISTS (SELECT 1 FROM fact_sources s WHERE s.fact_id=f.id
          AND s.record_id NOT IN (SELECT value FROM json_each(?)))""", (person["org_id"], cutoff, json.dumps(sorted(allowed)))):
        value = json.loads(row[1])
        if isinstance(value, str) and ("client", row[0]) in catalog:
            aliases[value.casefold()].add(row[0])
    entities, ambiguous = [], []
    client_ids = set()
    for alias, ids in {**aliases, **cities}.items():
        if len(alias) >= 2 and contains(question, alias):
            candidates = [catalog[("client", client)] for client in ids]
            if len(candidates) == 1:
                client_ids.update(ids)
            else:
                ambiguous.extend(candidates)
    # Unique prefixes support shortened client names, never cross-org names.
    for word in tokens(question):
        if len(word) >= 4:
            candidates = {entity.id for (kind, _), entity in catalog.items() if kind == "client" and any(part.casefold().startswith(word) for part in entity.name.split())}
            if len(candidates) == 1:
                client_ids.update(candidates)
    entities.extend(catalog[("client", client)] for client in sorted(client_ids))
    known_clients = [entity for (kind, _), entity in catalog.items() if kind == "client"]
    if not client_ids and len(known_clients) == 1 and re.search(r"\b(billing|launch)\b", question, re.I):
        client_ids.add(known_clients[0].id)
        entities.append(known_clients[0])
    query_tokens = tokens(question) - {word for client in client_ids for word in tokens(catalog[("client", client)].name)}
    for kind in ("task", "document", "sheet", "meeting"):
        candidates = [entity for (typ, _), entity in catalog.items() if typ == kind and (not client_ids or entity.client_id in client_ids)]
        exact = [entity for entity in candidates if contains(question, entity.id) or contains(question, entity.name)]
        if exact:
            entities.extend(exact)
            continue
        scores = [(len(query_tokens & tokens(entity.name)) / max(1, len(tokens(entity.name))), entity) for entity in candidates]
        scores = [(score, entity) for score, entity in scores if score >= 0.5]
        if not scores:
            continue
        best = max(score for score, _ in scores)
        winners = [entity for score, entity in scores if score == best]
        if len(winners) == 1:
            entities.extend(winners)
        elif kind == "task":
            ambiguous.extend(winners)
    full = [p for p in people.values() if contains(question, p["id"]) or contains(question, p["name"])]
    if full:
        duplicate_names = {p["name"].casefold() for p in full if sum(other["name"].casefold() == p["name"].casefold() for other in full) > 1}
        for p in full:
            item = Entity(p["id"], "person", p["name"], [r["id"] for r in records if r["author"] == p["id"] or r.get("owner") == p["id"]])
            (ambiguous if p["name"].casefold() in duplicate_names and not contains(question, p["id"]) else entities).append(item)
    else:
        first_names = defaultdict(list)
        for p in people.values():
            first_names[p["name"].split()[0].casefold()].append(p)
        for first, candidates in first_names.items():
            if contains(question, first):
                result = [Entity(p["id"], "person", p["name"], []) for p in candidates]
                (entities if len(result) == 1 else ambiguous).extend(result)
    # A directly resolved project entity also establishes client context.
    if not client_ids:
        inferred = {entity.client_id for entity in entities if entity.kind in {"task", "document", "meeting", "sheet"} and entity.client_id}
        if len(inferred) == 1:
            entities.append(catalog[("client", next(iter(inferred)))])
    dedup = lambda rows: list({(row.kind, row.id): row for row in rows}.values())
    return Resolution(dedup(entities), dedup(ambiguous), records, people)
