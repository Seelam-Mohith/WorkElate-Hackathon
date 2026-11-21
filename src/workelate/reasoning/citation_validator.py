"""Verify source access, time, and server-built support; reject model rewrites."""

import json

from workelate.memory.repository import MemoryRepository
from workelate.storage.repository import Repository
from workelate.time_utils import normalized_utc, parse_utc
from workelate.privacy.injection import instruction_text


def validate_card(conn, card, person_id, cutoff, *, through_day=None):
    repository = Repository(conn, through_day=through_day)
    sources = {source: repository.get_record(source, person_id) for source in card.sources}
    if not card.sources or any(source is None or parse_utc(source["at"]) > parse_utc(cutoff) or instruction_text(source["body"]) for source in sources.values()):
        return False
    supported = set()
    for proof in card.proofs:
        kind = proof["kind"]
        if kind in {"quote", "field"}:
            record = sources.get(proof["record"])
            if record is None:
                return False
            if kind == "quote" and (not proof["quote"].strip() or proof["quote"] not in record["body"]):
                return False
            if kind == "field" and record.get(proof["path"]) != proof["value"]:
                return False
            supported.add(proof["record"])
        elif kind == "memory":
            fact = conn.execute("SELECT observed_at,effective_at,extraction_method FROM knowledge_facts WHERE id=?", (proof["fact_id"],)).fetchone()
            evidence = {row[0] for row in conn.execute("SELECT record_id FROM fact_sources WHERE fact_id=?", (proof["fact_id"],))}
            if not fact or not evidence or not evidence <= sources.keys() or max(fact["observed_at"], fact["effective_at"]) > normalized_utc(cutoff):
                return False
            if fact["extraction_method"] == "feedback":
                from workelate.feedback.repository import feedback_fact_valid
                if not feedback_fact_valid(conn, proof["fact_id"], person_id, cutoff, through_day=through_day):
                    return False
            supported.update(evidence)
        elif kind == "commitment":
            item = next((item for item in MemoryRepository(conn, through_day=through_day).get_commitments(person_id, as_of=cutoff) if item["id"] == proof["commitment_id"]), None)
            if not item or item["status"] != proof["status"] or set(item["source_ids"]) != set(card.sources):
                return False
            supported.update(item["source_ids"])
        else:
            return False
    return supported == set(card.sources)


def validate_generated(response, cards, conn, person_id, cutoff, *, through_day=None):
    allowed = {card.id: card for card in cards}
    selected = []
    for claim in response.claims:
        card = allowed.get(claim.evidence_id)
        if card is None or claim.text != card.text or set(claim.sources) != set(card.sources):
            raise ValueError("Unsupported model claim")
        if not validate_card(conn, card, person_id, cutoff, through_day=through_day):
            raise ValueError("Unsupported citation")
        selected.append(card)
    return list({card.id: card for card in selected}.values())
