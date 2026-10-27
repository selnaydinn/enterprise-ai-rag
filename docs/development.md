# Development

## Running the backend without Docker

```bash
python -m venv venv
venv\Scripts\activate            # Linux/macOS: source venv/bin/activate
# On Linux, install the CPU-only torch first to avoid the large GPU build:
# pip install torch==2.12.1 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-dev.txt
uvicorn main:app --reload
```

`requirements.txt` contains the app dependencies (pinned); `requirements-dev.txt` adds the test and lint tools.
The Docker image uses Python 3.13 like the local venv and CI.

## Code layout

| Part | Where | Notes |
|---|---|---|
| API | `main.py`, `schemas.py` | Endpoints, request/response models, startup checks (`lifespan`) |
| Settings | `core/settings.py` | All settings in one `pydantic-settings` class |
| Model / DB / LLM | `core/config.py` | Created lazily once (`get_embeddings`, `get_db`, `get_llm`), loaded at startup |
| Ingestion | `services/ingestion.py` | PDF → chunks → ChromaDB, document deletion |
| Query | `services/query.py` | Retrieval, threshold, prompt, real token counting (`usage_metadata`) |
| Token limit | `services/usage.py` | Global token counter stored in SQLite |
| Frontend | `rag-frontend/` | `app/`, `components/`, `services/api.ts`, `types/` |
| Evaluation | `eval/` | See [`eval/README.md`](../eval/README.md) |

Uploads and deletions share one lock (`UPLOAD_LOCK` in `main.py`), so a deletion cannot run while the same document
is half-ingested. Embedding and Gemini calls run in a thread pool, so other requests are not blocked.

## Tests and code quality

```bash
python -m unittest discover tests      # backend unit tests
ruff check .                           # Python lint (rules in ruff.toml: E4, E7, E9, F)

cd rag-frontend
npx tsc --noEmit                       # type check
npm run lint                           # ESLint
```

- **No real services in tests.** `tests/_guards.py` (installed by `tests/__init__.py`) blocks creating the real
  Gemini client, ChromaDB connection or embedding model, and any network access outside localhost. The guard raises a
  `BaseException`, so it cannot be swallowed by an `except Exception` block and turned into a passing `500` test.
  Mock `get_db` / `get_llm` / `get_qa_chain` instead (see `tests/test_upload.py`, `tests/test_query.py`).
- **PDF-based tests** read the real `uploaded_pdfs/meb_yonetmelik.pdf` and are skipped when it is missing (e.g. in CI).
- **Frontend:** there are no unit tests yet; the frontend is checked with TypeScript and ESLint.

## CI

`.github/workflows/ci.yml` runs on every pull request to `main`:

- `backend-test`: Python 3.13, CPU-only torch, `requirements-dev.txt`, `ruff check .`, unit tests.
  There is no `.env` in CI, so the defaults from `core/settings.py` are used.
- `frontend-static-test`: Node 22, `npm ci`, `tsc --noEmit`, ESLint.

## Logging

All modules log to the `EnterpriseAI` logger; the level comes from `LOG_LEVEL`. With `LOG_LEVEL=DEBUG` you also see
chunks rejected by the score threshold and embedding progress. Questions are currently logged in full.
