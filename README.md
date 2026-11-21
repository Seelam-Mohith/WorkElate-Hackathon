# WorkElate – AI Workplace Assistant

WorkElate is a Python-based AI workplace assistant that remembers organizational information, answers questions using Hybrid RAG, generates meeting briefs, tracks commitments, learns from feedback, and supports privacy-aware forgetting.

## Key Features

- **Persistent Memory:** Stores workplace records, decisions, tasks, and commitments.
- **Hybrid RAG:** Combines SQLite FTS5 keyword search with ChromaDB vector search.
- **Smart Q&A:** Provides grounded answers with source citations.
- **Meeting Briefs:** Generates personalized meeting summaries, deadlines, and blockers.
- **Feedback Learning:** Incorporates user corrections and preferences.
- **Privacy & Forgetting:** Enforces access permissions and suppresses user-owned information.
- **Time-Aware Retrieval:** Answers questions using information available at the requested time.

## Tech Stack

- **Backend:** Python 3.11+
- **Database:** SQLite
- **Vector Database:** ChromaDB
- **Embeddings:** Hugging Face `all-MiniLM-L6-v2`
- **Retrieval:** Hybrid RAG (BM25 + Semantic Search)
- **LLM:** OpenAI-Compatible API (Optional)
- **Interface:** CLI, Streamlit, Local JSON API
- **Testing:** Pytest

## Installation

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,vector]"
```

Configure environment variables (PowerShell):

```powershell
$env:R3_PACK = (Resolve-Path .\BuildSprint-R3-candidate).Path
$env:WORKELATE_RUNTIME_DIR = Join-Path (Get-Location) '.workelate'
$env:WORKELATE_EMBEDDING_PROVIDER = 'huggingface'
$env:WORKELATE_VECTOR_BACKEND = 'chroma'
```

## CLI Commands

```bash
# Ingest daily workplace records
assist ingest --day 1

# Ask workplace questions
assist ask --person p02 --at 2026-11-04T18:00:00Z --ask "Who owns billing for NB?"

# Generate meeting briefs
assist brief --person p02 --date 2026-11-05

# Forget personal information
assist forget --person p02 --what "Forget my preference about meeting briefs"
```

## Architecture

```text
Workplace Records
       ↓
Data Ingestion → SQLite
       ↓
Memory & Embeddings
       ↓
Hybrid Search (FTS5 + ChromaDB)
       ↓
Privacy & Evidence Validation
       ↓
Grounded Answers / Meeting Briefs
```

## Testing

```bash
python -m pytest -q
python BuildSprint-R3-candidate/tools/run_days.py --cmd "assist" --days 1-5 --out runs/mine
python BuildSprint-R3-candidate/tools/score.py --run runs/mine --days 1-5
```

See [EVALUATION.md](EVALUATION.md) for evaluation results and [DESIGN.md](DESIGN.md) for technical architecture.

## Limitations

- Advanced language understanding requires a configured LLM.
- Forgetting suppresses information but does not securely erase raw records.
- Hidden evaluation days 6–15 are unavailable.

---

**WorkElate – An AI assistant that remembers, retrieves, learns, and respects privacy.**

<img width="1901" height="883" alt="image" src="https://github.com/user-attachments/assets/b6638b40-7e16-4102-b1c1-bc98b5f2a898" />
<img width="1510" height="773" alt="image" src="https://github.com/user-attachments/assets/f93d7287-6966-4abe-a4a4-3a4607e65003" />
<img width="1515" height="843" alt="image" src="https://github.com/user-attachments/assets/da209c93-f353-4840-a35f-e44b52d6bfd8" />
<img width="1538" height="695" alt="image" src="https://github.com/user-attachments/assets/c79619df-6261-486b-9454-c204dd4a9acb" />

