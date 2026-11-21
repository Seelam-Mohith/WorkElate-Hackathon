# Stage 6 evaluation and validation

Official tools: unmodified `BuildSprint-R3-candidate/tools/run_days.py` and `score.py`.
Days: 1-5. Command: `assist`. Chat provider: `none`. Embedding provider: `none`.
Runtime: `.workelate/stage6-final/`. Outputs: `runs/mine/` (ignored by Git).

Baseline Stage 5 score: **68.10/85**. Final Stage 6 score: **76.25/85**.
The CLI rounds this to one decimal; `score.json` retains the two-decimal total.

| Area | Score | Points | Checks |
| --- | ---: | ---: | ---: |
| briefs | 0.9831 | 19.66/20 | 7 |
| answers | 0.9394 | 14.09/15 | 33 |
| honesty | 1.0000 | 15.00/15 | 40 |
| memory | 0.5000 | 7.50/15 | 2 |
| privacy | 1.0000 | 10.00/10 | 40 |
| better | n/a | n/a | 5 |
| cost | 1.0000 | 10.00/10 | 40 |

All **45 harness calls** exited 0; every saved ask/brief output was valid JSON. No privacy leaks, honesty penalties or token-limit failures were reported.
Total recorded command time was 67.84 seconds; median command time was 734 ms on this machine. These measurements include process startup and are not a performance guarantee.
All ask/brief model input/output token counts and model costs were actually zero in this no-provider replay.
The getting-better component needs at least six scored days. The design note is graded by humans, separately out of 10. Neither component is claimed as passed.

## Remaining partial or failed cases

| Day | Item | Credit | Actual scorer finding |
| --- | --- | ---: | --- |
| 3 | `ev-nb-me-04` | 0.9444 | missed 1/6 changes, e.g. "Launch moved from 10 Nov to 14 Nov" |
| 4 | `ev-nb-ne-05` | 0.9375 | change "Homepage redesign is done" without its source; change "Analytics tagging is done" without its source; change "October invoice is done" without its source |
| 4 | `q04-02` | 0.0000 | no answer |
| 5 | `q05-02` | 0.0000 | no answer |

For `q04-02`, the assistant asks which client the user means: the available records do not explicitly associate Mumbai with a particular client, and the person timezones do not establish that geographic fact.
For `q05-02`, it asks which of the two same-first-name people is intended; the deck-recipient wording does not provide a unique supported identity. Both receive zero from the keyword scorer, and the latter also affects its small memory subset. The implementation does not add an unsupported mapping just to satisfy those keys.
The remaining brief deductions reflect capped evidence selection and differences between the chosen verified source and the scorer's preferred record. Valid alternate citations can still receive partial credit. The omitted launch item in the earlier brief is not fabricated to increase recall. No answer-key data is loaded by application code.

## Tests and operational checks

- `python -m pytest -q`: **343 passed**.
- New forgetting/security tests cover own-topic suppression, other-person/org isolation, restricted-author permissions, correction/preference removal, vector/FTS/cache cleanup, same-topic copies, no resurrection, pre-extraction delayed forget feedback, rollback, historical replay and separate CLI processes.
- Explicit-language tests use independent names and arbitrary deliverables/dates; they verify cautious fallback patterns, uncertainty rejection, delivery completion and explicit reassignment provenance.
- Existing suites cover briefs, citations, local dates, temporal facts, providers, usage limits, migrations and privacy.
- `python -m pip check`: no broken requirements. `git diff --check`: no whitespace errors.
- Editable package build/reinstallation succeeded offline using the already installed local setuptools backend. Both entry points were exercised from another working directory. The normal isolated-install attempt could not download setuptools because network sockets are restricted in this session; a clean online environment or preinstalled build dependencies are needed for that step.
- The five supplied feedback files contain no arrived forget requests during Days 1-5. Therefore official replay alone does not test forgetting; the independent end-to-end subprocess tests do.
- The original organizer data and scoring scripts were not edited.

## Changes in Stage 6

- Added `forgetting/__init__.py`, `forgetting/schema.py`, and `forgetting/service.py` for durable, transactional owner-scoped suppression.
- Updated `cli.py`, `ingestion/service.py`, `storage/migrations.py`, and `feedback/service.py` for the real command, migration/reset, replay prevention, and pre-extraction forget processing.
- Updated `privacy/access.py`; added `privacy/injection.py` for shared suppression checks and conservative instruction quarantine.
- Updated `memory/service.py` and `memory/structured_extractor.py`; added `memory/explicit_language.py` for safe retry behavior and narrow explicit promise/delivery fallback.
- Updated `reasoning/answer_engine.py`, `reasoning/evidence.py`, `reasoning/citation_validator.py`, `retrieval/entity_resolution.py`, `retrieval/temporal.py`, and `retrieval/indexing.py` for temporal bounds, relevance, evidence scope, injection handling and no resurfacing.
- Updated `briefs/candidates.py` and `briefs/service.py` for supported historical context, completion handling and corroborating citations.
- Added `tests/test_forgetting_security.py` and `tests/test_explicit_language.py`; updated CLI, memory, answer and feedback tests for the implemented behavior.
- Updated `README.md`; added `DESIGN.md` and this evaluation report.

## Limits of this validation

No live paid model, remote embedding model or hidden Days 6-15 was tested. General semantic quality depends on the configured provider. Without a provider, only narrow explicit language patterns are interpreted; general extraction remains visibly pending. Promise completion uses exact normalized deliverable matching.
Forgetting suppresses owned evidence and removes personal derived events, but retains immutable raw JSON and original pack files. It is not secure erasure. Topic paraphrases can fail to match, and a matching owned record is suppressed as a whole. A Day 1 reset intentionally starts a new run and clears suppression.
The CLI assumes the trusted harness supplies the person identity. It does not implement network-service authentication, React, or unrelated application features.
