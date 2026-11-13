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
| Authentication | `core/auth.py` | Password check, signed session token and cookie, `require_auth` dependency, login lockout (`LoginGuard`) |
| Settings | `core/settings.py` | All settings in one `pydantic-settings` class |
| Logging | `core/log_setup.py` | Text/JSON log format, request ID (`RequestIdMiddleware`, `X-Request-ID`), per-request summary line |
| Model / DB / LLM | `core/config.py` | Created lazily once (`get_embeddings`, `get_db`, `get_llm`, `get_reranker`), loaded at startup |
| Ingestion | `services/ingestion.py` | PDF → chunks (`split_pages`, `prepare_chunks`) → ChromaDB (`process_pdf`), document deletion |
| Database build | `scripts/build_db.py` | Builds a new database from the PDFs; see [configuration.md](configuration.md#building-a-database) |
| Query | `services/query.py` | Retrieval, threshold, reranking (`rerank_chunks`, shared `rerank_scores` with eval), prompt, real token counting (`usage_metadata`), startup warm-up (`warm_up`), per-question timing log (`Süreler: ...`) |
| Token limit | `services/usage.py` | Global token counter stored in SQLite |
| Frontend | `rag-frontend/` | `app/page.tsx` (login gate), `components/Workspace.tsx` (the app), `components/LoginForm.tsx`, `services/api.ts` (all requests, `credentials: "include"`, `401` → login screen), `types/` |
| Evaluation | `eval/` | See [`eval/README.md`](../eval/README.md) |

If an upload fails after chunks were written (an error halfway through embedding, or after it), `main.py`
`rollback_chunks` deletes that PDF's chunks by `pdf_hash` (`services/ingestion.py` `delete_chunks_by_hash`) before
removing the file, so no chunks without a file are left and the same PDF can be uploaded again. Chunk ids come from the
PDF hash and Chroma writes with upsert, so processing the same PDF again never duplicates chunks.

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
  Gemini client, ChromaDB connection, embedding model or reranker model, and any network access outside localhost. The
  guard raises a `BaseException`, so it cannot be swallowed by an `except Exception` block and turned into a passing
  `500` test. Mock `get_db` / `get_llm` / `get_qa_chain` / `get_reranker` instead (see `tests/test_upload.py`,
  `tests/test_query.py`).
- **Authentication in tests.** Endpoint tests call `bypass_auth(self, main)` (`tests/test_upload.py`) in `setUp`,
  which overrides `require_auth` for that test. Authentication itself is tested in `tests/test_auth.py`; it also checks
  that every endpoint except the public ones returns `401` without a session, so a new endpoint without
  `Depends(require_auth)` fails the tests.
- **PDF-based tests** read the real `uploaded_pdfs/meb_yonetmelik.pdf` and are skipped when it is missing (e.g. in CI).
- **Frontend:** there are no unit tests yet; the frontend is checked with TypeScript and ESLint.

## CI

`.github/workflows/ci.yml` runs on every pull request to `main`:

- `backend-test`: Python 3.13, CPU-only torch, `requirements-dev.txt`, `ruff check .`, unit tests.
  There is no `.env` in CI, so the defaults from `core/settings.py` are used.
- `frontend-static-test`: Node 22, `npm ci`, `tsc --noEmit`, ESLint.

## Dependency updates

- **Dependabot** (`.github/dependabot.yml`) opens pull requests for new versions: npm and pip weekly, GitHub Actions
  and the Docker base image monthly. Frontend minor/patch updates are grouped into one PR. Nothing is merged
  automatically; each PR runs CI. Vulnerability alerts and security PRs are enabled separately in the GitHub repository
  settings (Settings → Code security).
- **torch is ignored** by Dependabot: its version is pinned in three places (`requirements.txt`, `Dockerfile`,
  `.github/workflows/ci.yml`) and must be updated in all of them together, by hand.
- **Re-run the eval** before merging updates of `chromadb`, `sentence-transformers`, `langchain*` or `pypdf`. Passing
  unit tests do not show retrieval quality changes (different embeddings, search or extracted PDF text).
- **pip-audit** (in `requirements-dev.txt`) checks the backend requirements against known vulnerabilities. It is not
  part of CI; run it by hand, e.g. before a release:

  ```bash
  pip-audit -r requirements.txt
  ```

  Some reported issues may not apply to this project (for example, ChromaDB server/auth issues, since the app uses the
  embedded client). Check the description before acting.

## Logging

All modules log to the `EnterpriseAI` logger; the level comes from `LOG_LEVEL`. With `LOG_LEVEL=DEBUG` you also see
chunks rejected by the score threshold and embedding progress. Questions are logged in full (a deliberate choice);
passwords and API keys never are.

**Request ID.** Every request gets an ID (`core/log_setup.py`). A valid incoming `X-Request-ID` header (up to 64
characters, letters, digits and `-`) is kept; otherwise a new one is generated. The ID is returned in the
`X-Request-ID` response header (also on unhandled `500` errors) and appears in every log line of that request,
including code running in the thread pool (`/query/`, PDF processing) and uvicorn's access line. Log lines outside a
request (startup, model loading) show `-`. When a request finishes, one summary line is logged
(`GET /documents/ 200 (45 ms)`; `WARNING` for `5xx`). To follow one request, search the log for its ID.

**Format.** `LOG_FORMAT=text` (default):

```
2026-11-12 18:03:14 WARNING [a3f9c2...] EnterpriseAI: Gemini çağrısı başarısız (timeout, ReadTimeout).
```

`LOG_FORMAT=json` writes one JSON object per line (`ts` in UTC, `level`, `logger`, `request_id`, `msg`, and `exc`
with the traceback when there is an error), for tools such as Sentry or Loki. uvicorn's own logs use the same format.
