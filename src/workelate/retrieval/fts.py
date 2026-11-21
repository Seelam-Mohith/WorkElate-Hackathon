"""FTS5/BM25 over an authorized, cutoff-bounded corpus, including ranking stats."""

import re

from workelate.privacy.access import ACCESS_SQL
from workelate.time_utils import normalized_utc


def keyword_search(conn, question: str, person_id: str, cutoff, *, limit=12, through_day=None):
    words = list(dict.fromkeys(re.findall(r"[^\W_]+", question.casefold(), re.UNICODE)))[:32]
    if not words:
        return []
    query = " OR ".join('"' + word + '"' for word in words)
    # A global BM25 corpus would allow private/future term frequencies to influence
    # ranking. This temporary FTS corpus contains only the requesting user's view.
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.visible_fts USING fts5(record_id UNINDEXED,title,body,tokenize='porter unicode61')")
    conn.execute("DELETE FROM temp.visible_fts")
    # Use the durable FTS posting index to find matching IDs, without using its
    # global ranking statistics. SQL authorization precedes the local BM25 corpus.
    conn.execute("""INSERT INTO temp.visible_fts SELECT d.record_id,d.title,d.body FROM record_fts
        JOIN search_documents d ON d.record_id=record_fts.record_id
        JOIN records r ON r.id=d.record_id JOIN people p ON p.id=?
        WHERE record_fts MATCH ? AND """ + ACCESS_SQL + " AND r.occurred_at<=? AND r.source_day<=?",
        (person_id, query, normalized_utc(cutoff), through_day if through_day is not None else 2**31))
    return [row[0] for row in conn.execute("SELECT record_id FROM temp.visible_fts WHERE visible_fts MATCH ? ORDER BY bm25(visible_fts,0,3,1),record_id LIMIT ?", (query, limit))]
