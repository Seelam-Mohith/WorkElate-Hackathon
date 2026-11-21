"""The single record access predicate used by all user-facing record queries.

SQL aliases r (records) and p (requesting person) are fixed by Repository.
Restricted authors receive no implicit access.
"""

ACCESS_SQL = """
r.org_id = p.org_id AND (
    r.visibility = 'team'
    OR (r.visibility = 'private' AND r.author_id = p.id)
    OR (r.visibility = 'restricted' AND EXISTS (
        SELECT 1 FROM record_readers rr WHERE rr.record_id = r.id AND rr.person_id = p.id
    ))
)
"""
