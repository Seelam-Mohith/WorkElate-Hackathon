"""Resolve observations after authorization; never persist a global current value."""

from workelate.memory.store import dump


def resolve_history(observations: list[dict]) -> list[dict]:
    rows = [dict(item) for item in sorted(observations, key=lambda row: (
        row["effective_at"], row["observed_at"],
        0 if row["authority"] in {"snapshot", "baseline", "change"} else 1, row["id"]))]
    active = []
    task_rank = 0
    ranks = {"snapshot": 1, "baseline": 2, "change": 3}
    for row in rows:
        row["status"] = "superseded"
        authority = row["authority"]
        if authority == "feedback":
            active = [row]
            task_rank = 0
            continue
        if any(other["authority"] == "feedback" for other in active) and authority in {"snapshot", "baseline", "assertion"}:
            continue
        if authority in ranks:
            is_task_field = row["subject_type"] == "task" and row["predicate"] in {"owner", "due_date", "status"}
            rank = ranks[authority] if is_task_field else 1
            if is_task_field and rank < task_rank:
                continue
            simultaneous = active and rank == task_rank and all(
                other["effective_at"] == row["effective_at"] and other["authority"] in ranks for other in active)
            if not simultaneous:
                active = []
            active.append(row)
            task_rank = rank
        else:
            if authority == "correction" and row.get("replaces_value_json") is not None:
                # Explicit self-correction is evidence of replacement. A different
                # author's claim remains a competing observation, regardless of age.
                active = [other for other in active if not (
                    dump(other["value"]) == row["replaces_value_json"] and other["author_id"] == row["author_id"])]
            active.append(row)
    status = "current" if len({dump(row["value"]) for row in active}) <= 1 else "disputed"
    for row in active:
        row["status"] = status
    return rows
