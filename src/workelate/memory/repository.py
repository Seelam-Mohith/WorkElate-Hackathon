"""Authorized, time-bounded memory views built on Stage 2 record authorization.

Every influencing source must be visible before resolution, comparison, or matching.
Persistent rows are observations, not a global public/private current-value cache.
"""

import json
import sqlite3

from workelate.memory.store import TASK_STATES, dump
from workelate.memory.structured_extractor import artifact_delta
from workelate.memory.temporal import resolve_history
from workelate.storage.repository import Repository
from workelate.time_utils import normalized_utc


class MemoryRepository:
    def __init__(self, connection: sqlite3.Connection, *, through_day: int | None = None):
        self._connection = connection
        self._through_day = through_day
        self._sources = Repository(connection, through_day=through_day)

    def _context(self, person_id, as_of):
        person = self._sources.get_person(person_id)
        bound = normalized_utc(as_of) if as_of is not None else None
        accessible = {row["id"]: row for row in self._sources.get_accessible_records(person_id, until=bound)} if person else {}
        return person, accessible, bound

    def _evidence(self, table, key, row_id, accessible) -> list[str]:
        sources = [row[0] for row in self._connection.execute(f"SELECT record_id FROM {table} WHERE {key}=? ORDER BY record_id", (row_id,))]
        return sources if sources and all(source in accessible for source in sources) else []

    def _fact_rows(self, subject_id, predicate, context, *, subject_type=None, client_id=None) -> list[dict]:
        person, accessible, bound = context
        if person is None:
            return []
        sql = "SELECT * FROM knowledge_facts WHERE org_id=? AND subject_id=?"
        params = [person["org_id"], subject_id]
        sql += """ AND (extraction_method<>'feedback' OR EXISTS (
            SELECT 1 FROM personal_fact_links l JOIN feedback_processing p ON p.feedback_id=l.feedback_id
            WHERE l.fact_id=knowledge_facts.id AND l.person_id=? AND p.status='applied' AND l.available_day<=?))"""
        params.extend([person["id"], self._through_day if self._through_day is not None else 2**31])
        if predicate is not None:
            sql += " AND predicate=?"
            params.append(predicate)
        if subject_type is not None:
            sql += " AND subject_type=?"
            params.append(subject_type)
        if client_id is not None:
            sql += " AND client_id=?"
            params.append(client_id)
        sql += """ AND EXISTS (SELECT 1 FROM fact_sources s WHERE s.fact_id=knowledge_facts.id)
            AND NOT EXISTS (SELECT 1 FROM fact_sources s WHERE s.fact_id=knowledge_facts.id
            AND s.record_id NOT IN (SELECT value FROM json_each(?)))"""
        params.append(json.dumps(sorted(accessible)))
        if bound:
            sql += " AND effective_at<=? AND observed_at<=?"
            params.extend([bound, bound])
        result = []
        for row in self._connection.execute(sql, params):
            if bound and (row["effective_at"] > bound or row["observed_at"] > bound):
                continue
            sources = self._evidence("fact_sources", "fact_id", row["id"], accessible)
            if sources:
                fact = dict(row)
                fact["value"] = json.loads(fact.pop("value_json"))
                fact["source_ids"] = sources
                result.append(fact)
        return result

    def get_fact_history(self, subject_id: str, predicate: str, requesting_person_id: str, as_of=None, *, subject_type=None, client_id=None) -> list[dict]:
        context = self._context(requesting_person_id, as_of)
        groups = {}
        for item in self._fact_rows(subject_id, predicate, context, subject_type=subject_type, client_id=client_id):
            groups.setdefault((item["subject_type"], item["client_id"]), []).append(item)
        history = sorted([item for group in groups.values() for item in resolve_history(group)],
                         key=lambda item: (item["effective_at"], item["observed_at"], item["id"]))
        for fact in history:
            fact["changes"] = [{
                "previous_value": json.loads(row["previous_value_json"]), "new_value": json.loads(row["new_value_json"]),
                "changed_at": row["changed_at"], "source_ids": [row["change_source_id"]],
            } for row in self._connection.execute("SELECT * FROM fact_history WHERE fact_id=?", (fact["id"],))
                if row["change_source_id"] in context[1]]
        return history

    def _current(self, subject_id, predicate, context, *, subject_type=None, client_id=None):
        rows = self._fact_rows(subject_id, predicate, context, subject_type=subject_type, client_id=client_id)
        groups = {}
        for item in rows:
            groups.setdefault((item["subject_type"], item["client_id"]), []).append(item)
        if len(groups) > 1:
            return {"subject_id": subject_id, "predicate": predicate, "status": "ambiguous", "value": None,
                    "contexts": [{"subject_type": kind, "client_id": client} for kind, client in groups],
                    "source_ids": sorted({source for item in rows for source in item["source_ids"]})}
        history = resolve_history(rows)
        active = [row for row in history if row["status"] in {"current", "disputed"}]
        if not active:
            return None
        values = {dump(row["value"]) for row in active}
        return {"subject_id": subject_id, "predicate": predicate,
                "status": "current" if len(values) == 1 else "uncertain",
                "value": active[0]["value"] if len(values) == 1 else None,
                "candidates": active, "source_ids": sorted({source for row in active for source in row["source_ids"]})}

    def get_current_fact(self, subject_id: str, predicate: str, requesting_person_id: str, as_of=None, *, subject_type=None, client_id=None):
        return self._current(subject_id, predicate, self._context(requesting_person_id, as_of), subject_type=subject_type, client_id=client_id)

    def get_commitments(self, requesting_person_id: str, client_id: str | None = None, as_of=None) -> list[dict]:
        return self._commitments(self._context(requesting_person_id, as_of), client_id)

    def _commitments(self, context, client_id=None):
        person, accessible, bound = context
        if person is None:
            return []
        groups = {}
        for row in self._connection.execute("""SELECT c.org_id,c.kind,c.entity_id,o.*
            FROM commitments c JOIN commitment_observations o ON o.commitment_id=c.id WHERE c.org_id=?
            AND EXISTS (SELECT 1 FROM commitment_sources s WHERE s.observation_id=o.id)
            AND NOT EXISTS (SELECT 1 FROM commitment_sources s WHERE s.observation_id=o.id
              AND s.record_id NOT IN (SELECT value FROM json_each(?)))
            ORDER BY o.effective_at,o.id""", (person["org_id"], json.dumps(sorted(accessible)))):
            if (client_id is not None and row["client_id"] != client_id) or (bound and row["effective_at"] > bound):
                continue
            sources = self._evidence("commitment_sources", "observation_id", row["id"], accessible)
            if sources:
                item = dict(row)
                item["source_ids"] = sources
                groups.setdefault(row["commitment_id"], []).append(item)
        promises, result = [], []
        for commitment_id, observations in groups.items():
            latest = observations[-1]
            item = {key: latest[key] for key in ("owner_id", "description", "due_date", "status", "client_id", "kind", "entity_id")}
            item.update(id=commitment_id, created_at=observations[0]["effective_at"], updated_at=latest["effective_at"],
                        source_ids=list(latest["source_ids"]), completion_evidence=[], history=observations)
            if item["kind"] == "task":
                for predicate, target in (("owner", "owner_id"), ("due_date", "due_date"), ("status", "status")):
                    current = self._current(item["entity_id"], predicate, context, subject_type="task", client_id=item["client_id"])
                    if current and current["status"] == "current":
                        item[target] = TASK_STATES[current["value"]] if predicate == "status" else current["value"]
                        item["source_ids"] = sorted(set(item["source_ids"]) | set(current["source_ids"]))
                        corrections = [fact for fact in current.get("candidates", []) if fact["extraction_method"] == "feedback"]
                        if corrections:
                            item.setdefault("corrected_fields", []).append(target)
                            item["updated_at"] = max(item["updated_at"], *(fact["effective_at"] for fact in corrections))
                        if predicate == "status" and item["status"] == "completed":
                            item["completion_evidence"] = current["source_ids"]
                    elif current:
                        item[target] = "unknown" if target == "status" else None
                        item.setdefault("uncertain_fields", []).append(target)
            else:
                item["deliverable_key"] = latest["deliverable_key"]
                promises.append(item)
            result.append(item)
        for delivery in self._connection.execute("SELECT * FROM delivery_evidence WHERE org_id=? AND record_id IN (SELECT value FROM json_each(?)) ORDER BY effective_at,id", (person["org_id"], json.dumps(sorted(accessible)))):
            if delivery["record_id"] not in accessible or (bound and delivery["effective_at"] > bound):
                continue
            matches = [item for item in promises if item["owner_id"] == delivery["owner_id"]
                and item["client_id"] == delivery["client_id"] and item["deliverable_key"] == delivery["deliverable_key"]
                and item["created_at"] <= delivery["effective_at"]]
            if len(matches) == 1:
                item = matches[0]
                item["status"] = "completed"
                item["updated_at"] = delivery["effective_at"]
                item["source_ids"] = sorted(set(item["source_ids"]) | {delivery["record_id"]})
                item["completion_evidence"] = sorted(set(item["completion_evidence"]) | {delivery["record_id"]})
                item["history"].append({"status": "completed", "effective_at": delivery["effective_at"],
                                        "source_ids": item["source_ids"]})
            elif matches:
                # Repeated deliverables are ambiguous; never guess which promise closed.
                for item in matches:
                    item["completion_uncertain"] = True
                    item["uncertainty_source_ids"] = sorted({delivery["record_id"]} |
                        {source for candidate in matches for source in candidate["source_ids"]})
        return sorted(result, key=lambda item: (item["created_at"], item["id"]))

    def get_meeting(self, meeting_id: str, requesting_person_id: str, as_of=None):
        context = self._context(requesting_person_id, as_of)
        person, accessible, bound = context
        if person is None:
            return None
        versions = [dict(row) for row in self._connection.execute(
            "SELECT * FROM meeting_memory WHERE org_id=? AND meeting_id=? AND record_id IN (SELECT value FROM json_each(?)) ORDER BY observed_at,id", (person["org_id"], meeting_id, json.dumps(sorted(accessible))))
            if row["record_id"] in accessible and (bound is None or row["observed_at"] <= bound)]
        if not versions:
            return None
        result = dict(versions[-1])
        latest = [item for item in versions if item["observed_at"] == result["observed_at"]]
        result["status"] = "uncertain" if len({dump((item["title"], item["start_at"], item["end_at"], item["attendees_json"])) for item in latest}) > 1 else "current"
        if result["status"] == "uncertain":
            result["candidates"] = latest
        result["attendees"] = json.loads(result.pop("attendees_json"))
        sources = [row[0] for row in self._connection.execute(
            "SELECT record_id FROM meeting_sources WHERE org_id=? AND meeting_id=? ORDER BY record_id", (person["org_id"], meeting_id))
            if row[0] in accessible]
        result["source_ids"] = sources
        result["related_records"] = [accessible[source] for source in sources]
        decisions = []
        for row in self._connection.execute("SELECT DISTINCT subject_id,predicate FROM knowledge_facts WHERE org_id=? AND predicate LIKE 'decision:%'", (person["org_id"],)):
            decisions.extend(item for item in self._fact_rows(row[0], row[1], context) if set(item["source_ids"]) & set(sources))
        result["decisions"] = decisions
        result["commitments"] = [item for item in self._commitments(context) if set(item["source_ids"]) & set(sources)]
        result["history"] = [{**item, "attendees": json.loads(item["attendees_json"])} for item in versions]
        return result

    def get_artifact_versions(self, kind: str, entity_id: str, requesting_person_id: str, as_of=None) -> list[dict]:
        person, accessible, bound = self._context(requesting_person_id, as_of)
        if person is None:
            return []
        result = []
        for row in self._connection.execute("SELECT * FROM artifact_versions WHERE org_id=? AND kind=? AND entity_id=? ORDER BY version,observed_at,record_id", (person["org_id"], kind, entity_id)):
            if row["record_id"] in accessible and (bound is None or row["observed_at"] <= bound):
                item = dict(row)
                item["content"] = json.loads(item.pop("content_json"))
                item["source_ids"] = [item["record_id"]]
                result.append(item)
        return result

    def get_artifact_changes(self, kind: str, entity_id: str, requesting_person_id: str, as_of=None) -> list[dict]:
        versions = self.get_artifact_versions(kind, entity_id, requesting_person_id, as_of)
        return [{"field": field, "previous_value": old, "new_value": new,
                 "effective_at": max(previous["observed_at"], current["observed_at"]), "source_ids": [previous["record_id"], current["record_id"]]}
                for previous, current in zip(versions, versions[1:])
                for field, old, new in artifact_delta(previous["content"], current["content"], kind)]
