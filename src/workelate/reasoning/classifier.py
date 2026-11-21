"""Cheap, deterministic routing; complex wording retains the general fallback."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Question:
    category: str
    predicate: str | None


def classify(text: str) -> Question:
    q = text.casefold()
    owner = bool(re.search(r"\b(who|owner|owns|owned|handles|responsible)\b", q))
    deadline = bool(re.search(r"\b(due|deadline|when)\b", q))
    predicate = "billing_owner" if "billing" in q else "launch_date" if "launch" in q and any(word in q for word in ("date", "when", "launch")) else "owner" if owner else "due_date" if deadline else "status"
    if "before" in q and "meeting" in q:
        return Question("multi_hop", "status")
    if re.search(r"\b(changed|changes|happened|since|compare)\b", q):
        return Question("changes", predicate)
    if re.search(r"\b(decision|decisions|decided)\b", q):
        return Question("decisions", None)
    if re.search(r"\b(promise|promises|promised|commitment|commitments)\b", q):
        return Question("commitments", None)
    if owner and deadline:
        return Question("multi_hop", "owner_due")
    if owner:
        return Question("ownership", predicate)
    if deadline:
        return Question("deadline", predicate)
    if re.search(r"\b(status|ready|done|complete|completed|blocked|cancelled)\b", q):
        return Question("status", "status")
    if re.search(r"\b(meeting|attendees|attending)\b", q):
        return Question("meeting", None)
    return Question("general", None)
