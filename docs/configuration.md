# Configuration

## Backend settings (`.env`)

Copy `.env.example` to `.env` and fill in the values. All settings are defined in one `pydantic-settings` class in
`core/settings.py`; values come from `.env` (or environment variables), otherwise the defaults below are used.

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | — (required) | Gemini API key |
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Allowed frontend origins, comma-separated |
| `CHROMA_DIR` | `chroma_db_normalization` | ChromaDB folder, relative to the project root |
| `TOKEN_LIMIT` | `10000` | Global token limit per window |
| `TOKEN_WINDOW_HOURS` | `6` | Window length in hours (must divide 24) |
| `USAGE_DB_PATH` | `usage.db` | SQLite file for the token counter (created automatically) |
| `LLM_MODEL` | `gemini-2.5-flash` | Gemini model |
| `LLM_TEMPERATURE` | `0.2` | Gemini temperature |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` |
| `ALLOW_DELETE` | `false` | Enables document deletion (anyone can delete while there is no auth) |

Relative paths are resolved from the project root, so the same values work in Docker (`/app`) and locally.

### Settings kept in code

These live in `core/settings.py` with defaults and are intentionally not in `.env.example`:

| Setting | Default | Why it is not in `.env` |
|---|---|---|
| `embedding_model` | `intfloat/multilingual-e5-base` | Changing it requires rebuilding the database |
| `collection_name` | `langchain` | Tied to the existing database |
| `chunk_size` / `chunk_overlap` | `1200` / `300` | Changing them requires rebuilding the database |
| `retrieval_k` | `3` | Should be measured with the eval scripts |
| `score_threshold` | `0.72` | Should be measured with the eval scripts |
| `max_upload_mb` | `20` | Must match the frontend (`page.tsx`) |
| `max_question_length` | `500` | Must match the frontend (`ChatArea.tsx`) |
| `upload_dir` | `uploaded_pdfs` | Tied to the Docker bind mount |

### Startup checks

The app refuses to start if `GEMINI_API_KEY` is missing or `CHROMA_DIR` does not contain a ChromaDB
(`chroma.sqlite3`). It never creates an empty database by itself. An invalid value (e.g. `TOKEN_LIMIT=abc`,
`TOKEN_WINDOW_HOURS=5`, `LOG_LEVEL=VERBOSE`) also stops the app at startup.

## ChromaDB

The app needs an existing ChromaDB folder (`CHROMA_DIR`). Database folders and `uploaded_pdfs/` are not in git, so on
a new machine copy both folders into the project root. A script that builds the database from `uploaded_pdfs/` does
not exist yet.

## Frontend settings (`rag-frontend/.env.local`)

Optional. Copy `rag-frontend/.env.local.example` to `rag-frontend/.env.local`:

```
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
```

If the file is missing, `http://127.0.0.1:8000` is used. The value is embedded at build time, so restart the dev
server after changing it.

## Docker

`.env` is read when the container is created. After changing it, recreate the container:

```bash
docker compose up -d --force-recreate
```
