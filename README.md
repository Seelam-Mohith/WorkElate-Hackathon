# WorkElate - Final Python CLI

WorkElate ingests workplace records into local SQLite, answers grounded questions,
generates meeting briefs, learns from delayed feedback, and forgets owned information.
All four official commands work across separate processes. A local JSON API and a Streamlit test UI are included for manual testing.
The organizer pack in `BuildSprint-R3-candidate/` is not modified.

## Setup

Requires Python 3.11+, SQLite with FTS5, and a writable local runtime directory.

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
$env:R3_PACK = (Resolve-Path .\BuildSprint-R3-candidate).Path
$env:WORKELATE_RUNTIME_DIR = Join-Path (Get-Location) '.workelate'
assist --help
```

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
export R3_PACK="$PWD/BuildSprint-R3-candidate"
export WORKELATE_RUNTIME_DIR="$PWD/.workelate"
assist --help
```

The submission command is `assist`, or equivalently `python -m workelate.cli`.
Both entry points work outside this repository after installation. Set absolute pack
and runtime paths when changing directories. Windows timezone data is installed automatically.

## CLI examples

```bash
assist ingest --day 1
assist ingest --day 2
assist ingest --day 3
assist brief --person p02 --date 2026-11-05
assist ask --person p02 --at 2026-11-04T18:00:00Z --ask "Who owns billing for NB?"
assist forget --person p02 --what "Forget my preference about meeting briefs"
```

Every invocation prints exactly one JSON value to stdout. Logs and sanitized diagnostics
go to stderr. Successful commands exit 0; argument/configuration errors exit 2,
ingestion failures exit 4, and unexpected internal failures exit 1. Help is JSON too.
Ask/brief follow the exact official fields; forget returns exactly `{"ok": true}`.
A person with no authorized meetings receives `[]`. An unchanged meeting has
`status: "nothing_new"`, empty lists, and a null question. Meeting purpose appears
in `open_these[].why`, without adding a nonstandard summary field.

## Configuration

| Variable | Default / purpose |
| --- | --- |
| `R3_PACK` | Current directory; challenge pack root |
| `WORKELATE_RUNTIME_DIR` | `.workelate` in current directory; SQLite and local state |
| `WORKELATE_LOG_LEVEL` | `WARNING`; DEBUG, INFO, WARNING, ERROR, CRITICAL |
| `WORKELATE_LLM_PROVIDER` | `none` or `openai-compatible` |
| `WORKELATE_LLM_MODEL` | Empty; your deployed chat model name |
| `WORKELATE_LLM_URL` | `https://api.openai.com/v1/chat/completions`; compatible endpoint |
| `WORKELATE_LLM_API_KEY` | Empty; provider credential, never logged |
| `WORKELATE_LLM_TIMEOUT` | 30 seconds, maximum 120 |
| `WORKELATE_LLM_ATTEMPTS` | 2; extraction retries, maximum 3 |
| `WORKELATE_LLM_MAX_SOURCE_CHARS` | 16000; oversized semantic sources stay pending |
| `WORKELATE_LLM_INPUT_USD_PER_MILLION` | Optional input price for cost estimates |
| `WORKELATE_LLM_OUTPUT_USD_PER_MILLION` | Optional output price for cost estimates |
| `WORKELATE_EMBEDDING_PROVIDER` | `none` or `openai-compatible` |
| `WORKELATE_EMBEDDING_MODEL` | Empty; your deployed embedding model name |
| `WORKELATE_EMBEDDING_URL` | `https://api.openai.com/v1/embeddings`; compatible endpoint |
| `WORKELATE_EMBEDDING_API_KEY` | Empty; embedding provider credential |
| `WORKELATE_EMBEDDING_TIMEOUT` | 15 seconds, maximum 60 |
| `WORKELATE_EMBEDDING_BATCH_SIZE` | 16, maximum 64; same-permission batches |
| `WORKELATE_EMBEDDING_INPUT_USD_PER_MILLION` | Optional embedding input price |
| `WORKELATE_RETRIEVAL_DEPTH` | 12, maximum 50 |

For semantic extraction and general language understanding, configure your chosen
Chat Completions-compatible provider, model, endpoint and credentials. Configure
embeddings independently. HTTPS is required except for loopback HTTP endpoints;
redirects are rejected. No SDK or model download is needed. For example, in PowerShell:

```powershell
$env:WORKELATE_LLM_PROVIDER = 'openai-compatible'
$env:WORKELATE_LLM_MODEL = '<your-deployed-model>'
$env:WORKELATE_LLM_URL = '<your-compatible-HTTPS-chat-endpoint>'
$env:WORKELATE_LLM_API_KEY = '<your-key>'
$env:WORKELATE_EMBEDDING_PROVIDER = 'openai-compatible'
$env:WORKELATE_EMBEDDING_MODEL = '<your-embedding-model>'
$env:WORKELATE_EMBEDDING_URL = '<your-compatible-HTTPS-embedding-endpoint>'
$env:WORKELATE_EMBEDDING_API_KEY = '<your-key>'
```

No model is configured by default. The supplied-days evaluation uses `none` for both
providers, so its model tokens and cost are actually zero. General extraction remains
explicitly pending in that mode. Narrow, evidenced send/share promises, explicit
Hinglish deadline patterns, structured fields, dates, and business labels have conservative
fallbacks. This is not a substitute for the challenge's requested LLM language capability;
configure and declare the actual model/endpoint before a model-backed submission.
No live paid model has been validated in this environment.

## Architecture and persistence

- `ingestion/`: transactional metadata and immutable raw records, sequential days,
  source validation, canonical duplicate detection, and delayed feedback ingestion.
- `storage/` and `privacy/`: shared organization/visibility/suppression predicates,
  additive migrations, bounded timestamps, sanitized errors, and injection quarantine.
- `memory/`: sourced facts, task transitions, meeting/artifact history, commitments,
  explicit deliveries, and optional single-record semantic extraction.
- `retrieval/`: durable FTS5, permission-scoped BM25 statistics, optional cached
  embeddings/cosine search, entity resolution, and weighted reciprocal rank fusion.
- `reasoning/`: deterministic answers and optional model selection from verified
  evidence cards. Models cannot invent final answer text, source IDs, or tool actions.
- `briefs/`: local-date meeting selection, previous attended client meeting,
  changes/decisions/blockers/deadlines, pending promises, historical context, and caching.
- `feedback/`: one-day availability, personal preferences/verdicts/corrections,
  original evidence preservation, idempotence, and persistent usage/status records.
- `forgetting/`: owner-authorized suppression rules, removal of personal derived
  corrections/preferences/verdicts, search cleanup, and cache invalidation.

The database is `WORKELATE_RUNTIME_DIR/workelate.sqlite3`. Five additive migrations
preserve earlier source/history tables. Day 1 atomically resets the entire challenge
run, including suppression markers; later-day retries preserve them. Failed ingestion
or forget transactions roll back. Future day/feedback files and answer keys are never
opened by application code. Runtime directories inside pack data/answers/tools are rejected.

## Security, time and feedback

Team records are available only within their organization; private records only to
their author; restricted records only to listed same-organization readers. Restricted
authors receive no implicit access. Every supporting source is checked before fact
resolution, retrieval, citations and model prompts. Ingestion is a trusted offline
operation: semantic extraction handles one source at a time and embedding batches
share organization and permission scope. The CLI trusts `--person` as the harness's
requester identity; this local submission is not a multi-user authentication service.

Ask uses UTC `--at`, the person's timezone, effective/observation timestamps, and the
corresponding ingested source-day bound. Historical questions also bound facts to the
requested historical date. Briefs for day N+1 use at most day N sources and exclude
records recorded after the meeting, even during later replay. Authorized late UTC
records in a preparation file are retained. No wall-clock time decides challenge facts.

Feedback from day N arrives only during ingest N+1. Forget feedback is applied before
extraction; other feedback follows memory extraction. Corrections require supported
known identities, an authorized attended client meeting, and a verifiable prior value.
They affect the author's personal view, preserve history and raw evidence, and are
labelled `Your correction:`. Their original source citations anchor the prior claim
and meeting context; they are not represented as originally asserting the replacement.
Invalid or unsafe interpretations are rejected; unsupported language stays pending.
Preferences and `kept`/`ignored` verdicts affect personal relevance without changing access.

Obvious embedded assistant-control instructions are quarantined from extraction,
answer evidence and citations. Remaining source text is untrusted JSON data under
fixed system instructions. Schema/provenance checks and exact claim selection enforce
output boundaries. No model receives tools, credentials or filesystem access.

## Forget behavior

Supported requests name a topic or record ID in information the requester authored,
or explicitly target their preferences, corrections, or verdicts. Topic words match
case-insensitively with simple plural normalization; quoted/named phrases can narrow
requests. Generic "forget" alone does nothing. "Forget all my preferences" clears
personal preference events; a topic request preserves unrelated records and memories.
The same ownership and visibility checks apply even when a record ID is supplied.
Another person's statements cannot be forgotten by the requester.

Suppression is persistent and applies to historical reads too. Markers prevent retry,
reindexing and later same-author copies on the forgotten topic from resurfacing. FTS,
vectors, relevant embedding cache entries and affected brief caches are removed.
Personal correction facts and preference/verdict events are deleted. Unrelated users'
feedback and other organizations remain intact. The response does not reveal whether
a target exists. Unknown people or unmatched requests return the same official success
shape. Unsupported semantic paraphrases may match nothing; no broad deletion is inferred.

This implements suppression, not secure erasure: original immutable raw workplace and
feedback JSON remains locally retained behind suppression markers, as do the original
pack files. Do not treat the SQLite file as a redacted export. Rule markers contain
hashed topic tokens rather than original request text. A Day 1 reset starts a fresh run.

## Usage and limits

Ask preflights at most 4,000 cumulative input tokens; each meeting brief at most 8,000,
including query embeddings and all system/schema/evidence framing. UTF-8 byte counts
plus framing headroom provide conservative preflight bounds. Provider-reported token
counts are used when returned; missing counts use conservative UTF-8 bounds rather than
pretending they are provider measurements. Ask/brief audit rows identify estimated usage.
Reported costs or configured rates are used; unavailable costs stay null. Invalid model
responses retain their usage. Cache hits and no-model calls report zero tokens/cost.
Ingestion extraction/indexing and feedback have separate persisted usage statistics.

## Evaluation and tests

From the repository root with the virtual environment active:

```bash
python -m pytest -q
python BuildSprint-R3-candidate/tools/run_days.py --cmd "assist" --days 1-5 --out runs/mine
python BuildSprint-R3-candidate/tools/score.py --run runs/mine --days 1-5
```

Set `R3_PACK` and a dedicated `WORKELATE_RUNTIME_DIR` first. These are the unmodified
official tools; paths include the pack prefix because the application lives above it.
The harness runs each command in a new process and stores outputs plus `log.jsonl`;
the scorer writes `runs/mine/score.json`. Run artifacts are ignored by Git. The official
harness logs request arguments, so keep those artifacts private if using sensitive inputs.

See [EVALUATION.md](EVALUATION.md) for actual scores, failures and validation scope,
and [DESIGN.md](DESIGN.md) for the submission design note. Unit/integration tests cover
all record types, migrations, CLI processes, privacy, timezones, future-day leakage,
prompt boundaries, corrections, forgetting, replay, rollback and usage. Provider tests
use mocks. Hidden days 6-15 are unavailable; the five-day scorer cannot grade improvement
over six or more days or the human design-note component.

Known limitations include constrained language patterns without a provider, conservative
identity/geographic clarification, exact deliverable matching for promise completion,
whole-record suppression when a topic matches an owned record, and no secure deletion
of raw archives. Semantic model quality/latency depends on the chosen provider.
