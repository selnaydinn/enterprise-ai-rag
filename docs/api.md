# API

Interactive documentation with request/response schemas: http://localhost:8000/docs (Swagger).
Response models are defined in `schemas.py`; the matching frontend types are in `rag-frontend/types/api.ts`.

Every endpoint except `/`, `/auth/login` and `/auth/logout` requires a session (see [Authentication](#authentication));
without one it returns `401` before the endpoint runs, so no model, database or Gemini call is made.

| Method | Path | Description |
|---|---|---|
| `POST` | `/auth/login` | Sign in with the password (`{"password": "..."}`); sets the session cookie. Public |
| `POST` | `/auth/logout` | Clears the session cookie. Public |
| `GET` | `/auth/me` | `{"authenticated": true}` with a valid session, otherwise `401` |
| `POST` | `/query/` | Ask a question (`{"question": "..."}`, max 500 characters) |
| `POST` | `/upload-pdf/` | Upload a PDF (multipart field `file`, max 20 MB) |
| `GET` | `/documents/` | List uploaded PDFs: `[{"name": "..."}]` |
| `DELETE` | `/documents/{name}` | Delete a document and its chunks (`403` unless `ALLOW_DELETE=true`) |
| `GET` | `/usage/` | Token usage of the current window |
| `GET` | `/features/` | Feature flags for the frontend: `{"allow_delete": bool}` |
| `GET` | `/static-pdfs/{name}` | Serves an uploaded PDF inline (used by the preview `iframe`) |
| `GET` | `/` | Health message. Public |

## Authentication

Single-user password login (`core/auth.py`). The password (`APP_PASSWORD`) and the signing secret (`SESSION_SECRET`)
come from `.env`, see [configuration.md](configuration.md#authentication).

- **`POST /auth/login`** with the correct password returns `204` and a `session` cookie: `HttpOnly` (not readable from
  JavaScript), `SameSite=Lax` (not sent with requests started by other sites), `Max-Age` = `SESSION_HOURS`, and
  `Secure` when `COOKIE_SECURE=true`. A wrong password returns `401`. After 5 wrong passwords from the same client the
  login is locked for 15 minutes: every attempt returns `429` with the remaining minutes, even with the right password.
  The counter is in memory and resets when the app restarts. Passwords are never logged.
- **The cookie value** is `<expiry unix time>.<HMAC-SHA256 signature>`. The server stores no sessions; it checks the
  signature and the expiry on each request. Changing the expiry breaks the signature. A session cannot be revoked
  individually: logging out only removes the cookie from that browser. Changing `SESSION_SECRET` ends all sessions.
- **Browsers** send the cookie only when the request allows credentials: the frontend uses `fetch(..., { credentials:
  "include" })`, and the backend allows it for the origins in `CORS_ORIGINS`. The preview `iframe` sends it by itself.
  The frontend and backend must use the same host name (`localhost` for both, not `localhost` and `127.0.0.1`), see
  [configuration.md](configuration.md#frontend-settings-rag-frontendenvlocal).

```bash
curl -i http://localhost:8000/documents/                                     # 401
curl -i -c cookies.txt -H "Content-Type: application/json" \
     -d '{"password": "..."}' http://localhost:8000/auth/login               # 204 + Set-Cookie
curl -i -b cookies.txt http://localhost:8000/documents/                      # 200
```

## Responses

**`POST /query/`**

```json
{
  "status": "success",
  "message": "...",
  "answer": "...",
  "sources": [{ "source_name": "meb_yonetmelik.pdf", "page": 22, "score": 0.85, "raw_text": "..." }],
  "token_usage": 2070,
  "usage": { "used": 3250, "limit": 10000, "resets_at": "2026-10-24T18:00:00+03:00", "limited": false }
}
```

`status` is `success` (answer generated) or `no_result`. `no_result` has the same `message`, `answer` and empty
`sources` in both of its cases: no chunk passed the score thresholds (Gemini was not called, `token_usage` is 0), or
Gemini was called but found no answer in the chunks (it replied `BILGI_YOK`; `token_usage` is the tokens spent). The
server log tells the cases apart (`Sonuç yok: ...`).

When the database has no documents at all (built with `--allow-empty`, or every document was deleted), the answer
is `no_result` with its own `message` and `answer` ("Henüz yüklü doküman yok. Önce bir PDF yükleyin."), empty
`sources` and `token_usage` 0. This is checked by counting the stored chunks, only when the search returns no
candidates; if chunks exist but the search still returns nothing, the usual `no_result` answer is returned and a
`WARNING` is logged.

Gemini can also return an empty answer without an error (the question was blocked, a safety filter stopped the answer,
or the token limit was used up by thinking). This is also `no_result` with empty `sources`, but with its own `message`
and `answer` ("Bu soruya yanıt üretilemedi..."), because the information may be in the documents. `token_usage` is the
tokens spent. If Gemini returns text but does not finish normally (`finish_reason` other than `STOP`, e.g.
`MAX_TOKENS`, `SAFETY`, `RECITATION`), the answer is `success` with its sources, and the `answer` ends with
"(Yanıt tamamlanamadı; eksik olabilir.)". Both cases are logged as `WARNING` with the `finish_reason`.

**`POST /upload-pdf/`**: `{"filename", "total_pages", "total_chunks", "message"}`. `filename` is the name the file was
saved under (e.g. `Rapor.PDF` becomes `Rapor.pdf`).

**`DELETE /documents/{name}`**: `{"name", "deleted_chunks"}`.

**`GET /usage/`**: `{"used", "limit", "resets_at", "limited"}`. The limit resets on fixed Turkey-time windows
(every 6 hours by default: 00:00, 06:00, 12:00, 18:00). The last request may exceed the limit; the real usage is
recorded without clipping.

## Errors

Errors are returned as HTTP status codes with `{"detail": "..."}`:

| Code | When |
|---|---|
| `400` | Invalid file name, not a PDF |
| `401` | No session, or the session cookie is invalid or expired (the frontend shows the login screen). `/auth/login`: wrong password |
| `403` | Deletion disabled |
| `404` | Document not found (`DELETE /documents/{name}`, `GET /static-pdfs/{name}`) |
| `409` | Duplicate upload (same content under another name, or same name with different content) |
| `413` | File larger than 20 MB |
| `422` | Empty or too long question. `/upload-pdf/`: the file is a PDF but cannot be processed (corrupted, encrypted, or no extractable text such as a scanned PDF) |
| `429` | `/query/`: token limit reached (the message includes the reset time). Only the app's own limit; the frontend locks the input on `429`. `/auth/login`: too many wrong passwords (the message includes the minutes left) |
| `500` | Internal error (details are logged, never returned to the client). Also used for non-temporary Gemini errors such as an invalid API key or model name. Errors not handled by an endpoint return `{"detail": "Beklenmeyen bir hata oluştu."}` with CORS headers, so the frontend can read the message |
| `503` | `/query/`: Gemini is temporarily unavailable. Separate messages for Gemini's own quota or rate limit (Google `429`) and for server or connection errors (Google `5xx`, network) |
| `504` | `/query/`: Gemini did not answer within `llm_timeout_seconds` on any attempt |
