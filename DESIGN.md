# WorkElate design note

The submission command is `assist`. One local SQLite database holds immutable
source records, sourced observations, search representations, feedback events,
suppression markers, and usage audits. Each CLI invocation opens that database;
no server or process-local conversation state is required. Five additive migrations
preserve earlier data. Day 1 intentionally starts a fresh challenge run.

## Select evidence before generating language

Organization and visibility predicates run before user-facing record retrieval.
Team records require the same organization, private records require the author,
and restricted records require explicit membership in the reader list. Suppressed
records are excluded by the same predicate. Derived facts require every supporting
record to be accessible; a private correction cannot change a public viewer's
answer. There is no global current-fact cache shared between viewers.

Task transitions have priority over stale incidental snapshots. Fact observations,
old/new values, artifact versions, meeting relationships and explicit delivery
evidence remain separate. Conflicts and identity ambiguity are retained rather
than resolved by arbitrary row order. Relative dates use the requester's or source
author's IANA timezone as appropriate. A missed deadline never completes a promise.

Retrieval combines exact entity matches, structured memory, FTS5/BM25 and optional
embedding cosine search with weighted reciprocal rank fusion. BM25 statistics are
computed only over the authorized, time-bounded candidate corpus, preventing hidden
records from shaping ranks. Embeddings are cached by model, content and permission
scope. Fixed structured questions avoid generation and query embedding calls.

## What the model receives

Optional semantic ingestion sends one record, a same-organization person directory,
known client metadata, source-local date and a strict extraction schema. It never
sends an entire day or combines private evidence with a broader audience. Embedding
batches share organization and permission scope. Ingestion is a trusted offline
operation, not an end-user request to browse every organization's records.

Ask and brief generation send only bounded, authorized evidence cards plus the
question/meeting context and fixed system instructions. Each card contains verified
record citations and server-built text. The model selects existing cards; it cannot
introduce final claims, citations, permissions, tools or deletion operations.
Extraction output also needs exact supporting quotations and known identities.
Obvious embedded assistant-control instructions are quarantined before generation
or semantic ingestion. Model output is independently validated.

Ask has a cumulative 4,000-input-token budget and each meeting brief has 8,000,
including query embedding and prompt framing. Conservative UTF-8 bounds enforce
preflight limits; provider counts take precedence in actual usage. Invalid responses
retain usage and fail safely. Unknown costs stay null. Deterministic calls and cache
hits report zero model tokens. No provider is configured for the published replay;
the README explains how to enable compatible chat and embedding endpoints and
requires declaring the actual model before a model-backed submission.

## Meetings, feedback and temporal boundaries

Briefs select meetings on the person's local date and compare with the previous
attended meeting for that client. Recent decisions, verified transitions, blockers,
deadlines and open promises are prioritized. Important historical facts can support
a brief but cannot alone manufacture a new-change brief. A requested future launch
date is not treated as confirmed merely because a mail requests it; a later explicit
confirmation can retain that request as corroborating context.

Preparation-day limits apply even when replaying after later ingestion. Both source
observation time and fact effective time must be valid. An earlier question cannot
see a backdated record first delivered in a later day file. Calendar versions
recorded after the target meeting cannot change its brief. Challenge dates, not
the machine's wall clock, determine availability.

Feedback written on day N arrives only on ingest N+1. Preferences and verdicts affect
personal ranking. Valid corrections update only their author's view, retain the
original evidence and historical values, and are marked as personal corrections.
Their citations anchor the previous fact and meeting; the feedback fingerprint
privately records why the replacement is valid. Applied feedback is idempotent.

## Forgetting and failure recovery

A forget request can suppress accessible statements authored by that person and
their own feedback. It cannot delete another person's statement or cross an
organization boundary. Explicit preference/correction/verdict requests target only
that feedback category; topic requests match named words or record IDs. An ambiguous
generic request does not infer a broad deletion. Responses have the official
`{"ok": true}` shape without revealing target existence.

Persistent hashed-topic rules and record/feedback tombstones prevent reprocessing,
reindexing and later same-author copies from restoring forgotten topics. Source
authorization excludes their derived facts, commitments and aliases. Personal
correction facts and preference/verdict events are removed, affected FTS/vectors
and embedding-cache entries are cleared, and related brief caches are invalidated.
Newly available forget feedback runs before extraction can send its target to a
provider. All suppression changes are transactional.

This is suppression rather than secure erasure: immutable raw JSON and the original
pack files remain on disk. Topic matching is conservative and does not understand
every semantic paraphrase. Authentication is supplied by the local harness's
`--person` identity; a hosted deployment would need a separate authenticated boundary.

The evaluation uses the unmodified official harness/scorer. Application code never
reads answer keys or future files. Independent fixtures cover privacy, time, prompt
injection, delayed feedback, forgetting, rollback, usage and subprocess persistence.
Actual scores and remaining failures are recorded in `EVALUATION.md`; hidden days
and live paid-model quality are not claimed as tested.
