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
| `ALLOW_DELETE` | `false` | Enables document deletion for the signed-in user |
| `AUTH_ENABLED` | `true` | Requires login; `false` only for local development (a warning is logged) |
| `APP_PASSWORD` | — (required when auth is on) | Login password |
| `SESSION_SECRET` | — (required when auth is on) | Signs the session cookie; at least 32 characters, never shared |
| `SESSION_HOURS` | `12` | Session length; after it the password is asked again |
| `COOKIE_SECURE` | `false` | `true` sends the cookie over HTTPS only; keep `false` on `http://localhost` or the login will not stick |

Relative paths are resolved from the project root, so the same values work in Docker (`/app`) and locally.

### Authentication

Generate the secret once and put it in `.env` (not in `.env.example`, which is in git):

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`.env` values need no quotes. Avoid spaces, `#` and quotes in the password so it is read the same way locally and by
Docker. Changing `SESSION_SECRET` signs everyone out. How the login and the cookie work: [api.md](api.md#authentication).

### Settings kept in code

These live in `core/settings.py` with defaults and are intentionally not in `.env.example`:

| Setting | Default | Why it is not in `.env` |
|---|---|---|
| `embedding_model` | `intfloat/multilingual-e5-base` | Changing it requires rebuilding the database |
| `collection_name` | `langchain` | Tied to the existing database |
| `chunk_size` / `chunk_overlap` | `1200` / `300` | Changing them requires rebuilding the database |
| `chunking_strategy` | `page` | How PDFs are split (`services/ingestion.py` `CHUNKING_STRATEGIES`). Must match the strategy the database was built with, otherwise new uploads are split differently from the rest |
| `retrieval_k` | `3` | Chunks sent to Gemini; should be measured with the eval scripts |
| `score_threshold` | `0.72` | e5 similarity threshold, applied before the reranker; should be measured with the eval scripts |
| `reranker_enabled` | `true` | Reranks the candidates with a cross-encoder; when `false`, only `retrieval_k` chunks are retrieved in vector order |
| `reranker_model` | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` | Should be measured with the eval scripts |
| `rerank_candidate_k` | `10` | Candidates retrieved for the reranker; more candidates are slower (15: about 3.7 s per question on CPU) |
| `llm_thinking_budget` | not set | Gemini thinking budget in tokens: not set = model default (dynamic), `-1` dynamic, `0` off, `N` upper limit. Lowers latency and token use but may change answer quality; measure end to end before changing. For a quick test: `$env:LLM_THINKING_BUDGET = "0"` before `uvicorn main:app` |
| `llm_timeout_seconds` | `20` | Timeout for each Gemini attempt (the library has none by default) |
| `llm_max_attempts` | `2` | Total Gemini attempts, `1` = no retry (the library default is 6). Worst case wait ≈ attempts × timeout. After the last attempt the API returns `504` (timeout) or `503` (quota, server or connection error), see [api.md](api.md#errors) |
| `rerank_score_threshold` | `-2.0` | If the best reranker score of the chunks that passed `score_threshold` is below this, Gemini is not called. Raw model score; measured with `eval/evaluate_negative.py`, re-measure when the model changes |
| `max_upload_mb` | `20` | Must match the frontend (`page.tsx`) |
| `max_question_length` | `500` | Must match the frontend (`ChatArea.tsx`) |
| `upload_dir` | `uploaded_pdfs` | Tied to the Docker bind mount |

### Startup checks

The app refuses to start if `GEMINI_API_KEY` is missing or `CHROMA_DIR` does not contain a ChromaDB
(`chroma.sqlite3`). It never creates an empty database by itself. With `AUTH_ENABLED=true` it also refuses to start if
`APP_PASSWORD` or `SESSION_SECRET` is missing, before loading the models, so a forgotten setting never leaves the app
open. An invalid value (e.g. `TOKEN_LIMIT=abc`, `TOKEN_WINDOW_HOURS=5`, `LOG_LEVEL=VERBOSE`, a `SESSION_SECRET` shorter
than 32 characters) also stops the app at startup.

After loading the models, the app runs one search and one rerank with a fixed question (`WARM_UP_QUESTION` in
`services/query.py`) so the first real question does not pay for model initialization and building the search index
(about 15 s measured on the first question in Docker). Gemini is not called and no tokens are used. If the warm-up
fails, the app does not start.

## ChromaDB

The app needs an existing ChromaDB folder (`CHROMA_DIR`) and never creates one by itself: with a wrong `CHROMA_DIR` it
would otherwise start with an empty database and answer every question with "no information". Database folders and
`uploaded_pdfs/` are not in git.

### Building a database

`scripts/build_db.py` builds a database from the PDFs into a **new** folder:

```bash
python scripts/build_db.py --target chroma_db_new                    # from uploaded_pdfs/
python scripts/build_db.py --target chroma_db_new --allow-download   # new machine: also downloads the models
python scripts/build_db.py --target chroma_db_new --allow-empty      # no documents yet: empty database
```

| Option | Default | |
|---|---|---|
| `--target` | required | New database folder. If it (or a leftover `<target>.partial`) exists, the script stops; nothing is overwritten |
| `--pdf-dir` | `upload_dir` (`uploaded_pdfs`) | PDF folder |
| `--chunking` | `chunking_strategy` (`page`) | Chunking strategy |
| `--allow-download` | off | Download missing models (e5 and the reranker) from HuggingFace. Without it the script runs offline and stops with a clear error if a model is not cached |
| `--allow-empty` | off | Build an empty database when the PDF folder is empty or missing; PDFs are then uploaded in the app. Has no effect when there are PDFs (the script says so) |
| `--manifest` | `eval/results/<target>_build.json` | Build record (see below) |

The PDFs go through the app's own code (`services/ingestion.py` `prepare_chunks` / `process_pdf`), with the same
embedding model, collection and Chroma settings. Steps:

1. **Checks:** the target must not exist, the PDF folder must contain PDFs (unless `--allow-empty`), no two PDFs may
   have the same content (the app also rejects that with `409`), and the target must not be `CHROMA_DIR`.
2. **Chunking** without embedding: chunk counts and lengths are printed; chunks over e5's 512-token limit are reported
   as a warning (their end is not embedded).
3. **Build** into `<target>.partial`, with progress and estimated time left.
4. **Verification** on a copy: count, ids, texts and metadata match the planned chunks, stored vectors match the model,
   every chunk is searchable, distance metric is `l2`. If anything fails, the script stops and leaves `<target>.partial`
   for inspection; the target is not created.
5. **Finish:** `<target>.partial` is renamed to `<target>`, the manifest is written, and the built PDFs are listed.

The app's database (`CHROMA_DIR`) is fingerprinted before and after the run and must be unchanged. Rebuilding the
current database with the default options reproduces it exactly (checked on 2026-11-07: same 638 chunks, ids, texts and
metadata, identical evaluation results). Building the 4 PDFs took about 9 minutes on CPU.

The **manifest** is a JSON record of the build: date, target, PDFs (name, hash, pages, chunks), chunking strategy and
sizes, model, chunk and token statistics, verification results, timings and library versions.

### Switching the app to a new database

The script never changes which database the app uses. To switch, set `CHROMA_DIR=<target>` in `.env` and restart the
app (in Docker, recreate the container). Keep the old folder until the new one is checked; switching back is the same
step. The new database contains only the PDFs in the PDF folder, which the app keeps in sync with uploads and deletions;
the script lists the PDFs it built so the list can be compared before switching.

## Frontend settings (`rag-frontend/.env.local`)

Optional. Copy `rag-frontend/.env.local.example` to `rag-frontend/.env.local`:

```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

If the file is missing, `http://localhost:8000` is used. The value is embedded at build time, so restart the dev
server after changing it.

Use the same host name for the frontend and the backend: open the app at `http://localhost:3000` and keep
`localhost` here. Browsers treat `localhost` and `127.0.0.1` as different sites and do not send the `SameSite=Lax`
session cookie between them; the login then seems to work, but every following request returns `401` and the login
screen comes back.

## Docker

`.env` is read when the container is created. After changing it, recreate the container:

```bash
docker compose up -d --force-recreate
```

### Model cache

The embedding and reranker models (about 1.6 GB) are stored in the named volume `hf_cache`, mounted at `/models`
(`HF_HOME=/models`). It lives in Docker, not in the project folder, and survives container rebuilds.

The container runs with `HF_HUB_OFFLINE=1`: models are read only from the volume and no requests are sent to
HuggingFace at startup, so the app also starts without internet. When the volume is empty (first setup, after
`docker volume rm`, or after changing a model) the models cannot be downloaded and the app does not start.

On a first setup, build the database inside the container so the models are downloaded into the volume:

```bash
docker compose run --rm rag-backend python scripts/build_db.py --target chroma_db_new --allow-download
```

`--allow-download` turns `HF_HUB_OFFLINE` off for that run only; the app itself stays offline. The project folder is
mounted at `/app`, so the database and the manifest are written to the project folder on the host. Then set
`CHROMA_DIR=chroma_db_new` in `.env` and start the app. If only a model changed and no database needs to be built, set
`HF_HUB_OFFLINE=0` in `docker-compose.yml`, start once, then set it back to `1`.

The container is limited to 3 GB RAM and 2 CPUs. With both models loaded the app uses about 1.6 GB (about 1.85 GB
while embedding an upload; measured locally), and reranking is slower than on an unrestricted CPU.
