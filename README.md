# WorkElate — Stage 2

WorkElate is a Python CLI foundation for a workplace assistant that will eventually
remember workplace changes and generate grounded meeting briefs and answers.
Stage 1 established packaging, argument validation, configuration, and logging.
Stage 2 adds real daily ingestion, local SQLite persistence, source validation,
and permission-filtered database access. Brief, ask, and forget remain unchanged
Stage 1 placeholders. There are no AI calls or extracted facts.

Requires Python 3.11 or later. The original challenge pack, including its README,
data, answers, and tools, remains untouched in `BuildSprint-R3-candidate/`.

## Install

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
$env:R3_PACK = (Resolve-Path .\BuildSprint-R3-candidate).Path
```

Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
export R3_PACK="$PWD/BuildSprint-R3-candidate"
```

Activation is optional when using the virtual environment's Python or executable
directly. Once installed, both entry points work from another directory.

## Commands

```bash
assist ingest --day 1
assist ingest --day 2
assist ingest --day 3
assist brief --person p02 --date 2026-11-03
assist ask --person p02 --at 2026-11-04T18:00:00Z --ask "Who owns billing?"
assist forget --person p02 --what "my travel preference"
python -m workelate.cli ingest --day 1
assist --help
```

Dates must be `YYYY-MM-DD`. Timestamps must include date, `T`, hours, minutes,
seconds, and UTC `Z` or `+00:00`; fractional seconds are accepted. Person IDs,
questions, and forget requests must be nonempty. IDs are not restricted to today's
known people. The schema layer preserves the full question as a single string,
removing only leading and trailing whitespace.

Every invocation writes exactly one deterministic JSON value to stdout, including
help and expected errors. Logs and concise diagnostics go to stderr. Errors never
echo argument values, private contents, exception details, or tracebacks.

| Exit | Meaning |
| --- | --- |
| 0 | Ingestion completed successfully, or help displayed as a JSON object |
| 1 | Unexpected internal error; sanitized JSON error |
| 2 | Invalid arguments or configuration; JSON error |
| 3 | Valid invocation of a Stage 1 operation that is not implemented |
| 4 | Ingestion failure; structured JSON error with safe source location when available |

`ingest` returns actual processed counts, for example:

```json
{"day":1,"feedback_processed":0,"ok":true,"records_processed":354}
```

`forget` still returns `{"error":"not_implemented","command":"forget"}`.
`brief` returns `[]` with exit 3 and a stderr warning: this is a list-shaped
placeholder, not a claim that no meetings exist. `ask` returns the official fields
`answer`, `matched`, `clarify`, `refused`, and `usage`. Its answer explicitly says
question answering is not implemented, with empty citations and matches, null
clarify/refused, and zero usage. It contains no workplace answer or mock AI output.
Zero milliseconds represents no model work; no cost or tokens are consumed.
Placeholders live in `placeholders.py`, separate from parsing and validation;
ingestion is dispatched to the independently reusable `ingestion.service.ingest_day`.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `R3_PACK` | Current working directory | Challenge pack root |
| `WORKELATE_RUNTIME_DIR` | `.workelate` in current working directory | Local runtime directory; contains `workelate.sqlite3` |
| `WORKELATE_LOG_LEVEL` | `WARNING` | DEBUG, INFO, WARNING, ERROR, or CRITICAL |

Paths expand `~` and resolve relative to the current working directory. Ingestion
creates the runtime directory and SQLite schema automatically. Configuration loading
and the other commands do not create state. To reuse a database when changing
working directories, set `WORKELATE_RUNTIME_DIR` to the same absolute path.
Runtime paths inside the pack's `data/`, `answers/`, or `tools/` are rejected.
No API key is needed. Timezone support uses `zoneinfo`;
the small `tzdata` dependency supplies IANA timezone data on Windows.

## Tests

```bash
python -m pytest -q
```

Tests run both installed entry points from temporary directories, validate command
inputs and JSON output, check configuration, imports, timezone availability,
logging isolation, safe failures, and absence of side effects in placeholder commands.
Independent fixture packs cover all eight record types, metadata synchronization,
raw JSON preservation, historical versions, duplicate conflicts, transactional reset,
rollback, delayed feedback, restricted readers, cross-organization isolation, UTC
query bounds, and local meeting dates. SQLite persists across subprocess calls.
Runtime dependencies are Pydantic 2 and Windows-only tzdata; pytest is a development
dependency. No web framework, model SDK, embeddings, or vector database is installed.

## Daily ingestion and persistence

`ingest_day(day, settings)` opens the local SQLite database with foreign keys enabled
and starts an immediate transaction. It synchronizes organization, people, and client
metadata, streams and validates `data/days/day-NN.jsonl`, stores immutable records
and restricted-reader relationships, and ingests `data/feedback/day-(N-1).jsonl`
only for N > 1. It records completion and commits everything together. Failures
roll back all changes for the day, including metadata and any Day 1 reset.

Day 1 starts a fresh run by clearing this application's tables inside the transaction.
A failed reset leaves the previous run intact. No runtime directory is recursively
deleted. Day N > 1 requires a completed Day N-1; gaps return
`previous_day_not_ingested`. Reprocessing a later day is supported. Records keep
their first source day/line; a duplicate ID with identical JSON content is accepted
without inserting another row. Conflicting content returns `conflicting_record_id`
and rolls back the day. Reordered object keys and formatting do not create conflicts.
Counts report validated source entries processed, including identical duplicates,
rather than only newly inserted rows.

Metadata files, the requested records file, and previous-day feedback for N > 1
are required. Feedback files may be empty. Missing files return
`missing_source_file`; invalid JSON or validation failures identify a filename and,
for JSONL, a physical line where available. Source contents and database exception
details never appear in CLI diagnostics. Blank JSONL lines are ignored; duplicate
JSON keys, non-finite numbers, and malformed records are rejected.

Each complete source object is preserved in `raw_json`, including unknown fields,
original timestamps, and type-specific history. Source JSONL text is retained apart
from its line ending. Common fields are mandatory. Mail subject/recipients, chat
channel, notification recipients, and person team are optional. Document, sheet,
task, event, and note fields follow the official structured format. External authors,
`system`, and null clients are accepted. Organization/client/reader/feedback-person
references must exist in metadata. An empty restricted-reader list grants no access.

Feedback stores source day, physical line, and SHA-256 of canonical JSON. Reprocessing
the same line is idempotent; changed content at an existing source position returns
`conflicting_feedback`. Identical feedback on distinct source lines is preserved.
Corrections, preferences, verdicts, and forget requests are stored without applying
them. Source files are treated as immutable historical input. Ingestion never opens
task files, answer files, or future record/feedback files.

## Database schema and access

The complete executable DDL is in `src/workelate/storage/schema.py`.

| Table | Columns |
| --- | --- |
| `organizations` | `id` PK, `name` |
| `people` | `id` PK, `org_id` FK, `name`, `role`, nullable `team`, `timezone` |
| `clients` | `id` PK, `name` |
| `records` | `id` PK, `org_id` FK, `record_type`, normalized `occurred_at`, `original_at`, `author_id`, nullable `client_id` FK, `visibility`, `body`, `source_day`, `source_line`, `raw_json` |
| `record_readers` | `record_id` FK, `person_id` FK; composite PK |
| `feedback` | `id` integer PK, `source_day`, `source_line`, `fingerprint`, `person_id` FK, `feedback_type`, `words`, nullable `meeting_id`/`topic`/`verdict`, `raw_json`; unique source day/line |
| `ingestion_history` | `day` PK, `status`, `records_processed`, `feedback_processed`, `completed_at` |

IDs, dates, timestamps, JSON, and source text use SQLite TEXT; days, source lines,
and counts use INTEGER. Visibility, record/feedback types, positive source positions,
and completed history status have CHECK constraints. Foreign keys protect references.
Authors have no people FK because external addresses and system are valid.
Indexes support organization/time, organization/client/time, organization/type,
reader, person-organization, and person-feedback lookups.

Privileged ingestion lives in `ingestion/service.py`. User-facing record access
lives in `storage/repository.py`, using the shared SQL predicate in
`privacy/access.py`. Every record query requires a requesting person and applies
organization isolation before returning JSON: team records allow same-org people,
private records allow their author only, restricted records allow listed readers
only. Restricted authors gain no implicit access, and cross-org readers gain none.
Unknown people receive no records. `get_person` returns directory metadata only.

Example for trusted application code:

```python
from workelate.config import load_settings
from workelate.ingestion.service import database_path
from workelate.storage.database import connect_database
from workelate.storage.repository import Repository

with connect_database(database_path(load_settings()), read_only=True) as connection:
    repository = Repository(connection)
    records = repository.get_records_for_client(
        "p02", "northbeam", until="2026-11-04T18:00:00Z"
    )
    meetings = repository.get_events_for_person("p02", date="2026-11-05")
```

Repository results are original source objects, not generated answers or briefs.
Time bounds are inclusive and normalized to UTC with fixed microsecond precision.
`time_utils.py` provides timezone-aware UTC parsing and conversion to a person's
local calendar date using IANA zones, including daylight saving. Source UTC dates
are never assumed to equal the local day-file date. Meeting filtering uses attendees
and each person's local date. Ingestion timestamp fractions support up to six digits;
the original source string remains intact.

## Harness compatibility and limitations

The official README, DATA_FORMAT, `tools/run_days.py`, and baseline were read before
implementation. The harness splits `--cmd` using `shlex.split`, executes without a
shell, adds command arguments, retains its current working directory, and starts a
fresh process for each call. Quote executable paths containing spaces; `assist`
must be on PATH when supplied as the command. Set `R3_PACK` explicitly when running
outside the pack: the harness has a script-location fallback, while this CLI follows
the documented current-directory fallback.

Eventually the installed command can be used as follows:

```bash
python BuildSprint-R3-candidate/tools/run_days.py --cmd "assist" --days 1-5 --out runs/mine
```

Ingestion now meets the harness success contract. Brief, ask, and forget still exit
3, which the harness records as failure even though their JSON parses. Stage 2 does
not pass or claim to pass the full official evaluation. The full Days 1–5 challenge
pack is available; hidden Days 6–15 are not distributed.

Only Stage 2 was added. There is no LLM integration, embeddings, semantic/RAG retrieval,
entity resolution, extraction, generated brief/answer, feedback-based memory update,
actual forgetting, API server, or React dashboard. The existing placeholder wording
still references Stage 1 to preserve its interface. No Stage 3 work is implemented.
