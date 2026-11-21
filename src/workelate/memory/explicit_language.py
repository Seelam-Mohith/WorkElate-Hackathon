"""Narrow no-provider fallback for explicit send/share statements, not general NLP."""

import re
from datetime import timedelta

from workelate.time_utils import parse_calendar_date, utc_to_local_date

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def extract_explicit(conn, record):
    person = conn.execute("SELECT timezone FROM people WHERE id=? AND org_id=?", (record["author"], record["org"])).fetchone()
    if not person or record["type"] not in {"chat", "mail"}:
        return None
    body = record["body"].strip()
    text = re.sub(r"^Hi (?:team|all)[,!.]?\s*", "", body, flags=re.I)
    first = re.split(r"[.!\n]", text, maxsplit=1)[0]
    if re.search(r"\b(?:maybe|might|could|if|unless|not|won't|cannot|can't|draft|example)\b|\?", first, re.I):
        return None
    delivery = re.match(r"Sharing (?:the )?([^.!?\n]{2,100}?) as promised\.", text, re.I)
    if delivery:
        return {"deliveries": [{"deliverable": delivery[1].strip(), "source_ids": [record["id"]], "evidence_quote": delivery[0]}]}
    match = re.match(r"(?:(?:I (?:will|'ll)|Will) (?:send|share) (?:the )?([^.!?\n]{2,100}?) (?:by (?:EOD )?)?(today|tomorrow|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|\d{4}-\d{2}-\d{2}))\b", text, re.I)
    hinglish = re.match(r"(?:the )?([^.!?\n]{2,100}?) kal tak bhej dunga\b", text, re.I)
    next_weekday = re.match(r"(?:the )?([^.!?\n]{2,100}?) next (Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday) tak share kar (?:dungi|dunga)(?:/dunga)?(?:,\s*pakka)?", text, re.I)
    if not match and not hinglish and not next_weekday:
        return None
    if not re.match(r"\s*(?:[.!]|$)", text[(match or hinglish or next_weekday).end():]):
        return None
    local = utc_to_local_date(record["at"], person[0])
    target, due_text = (match[1], match[2].casefold()) if match else (hinglish[1], "tomorrow") if hinglish else (next_weekday[1], next_weekday[2].casefold())
    if due_text in {"today", "tomorrow"}:
        due = local + timedelta(days=due_text == "tomorrow")
    elif due_text in WEEKDAYS:
        offset = (WEEKDAYS.index(due_text) - local.weekday()) % 7
        due = local + timedelta(days=offset or (7 if next_weekday else 0))
    else:
        due = parse_calendar_date(due_text)
    return {"commitments": [{"description": "Send " + target.strip(), "deliverable": target.strip(),
        "due_date": due.isoformat(), "source_ids": [record["id"]], "evidence_quote": (match or hinglish or next_weekday)[0]}]}
