# API

Interactive documentation with request/response schemas: http://localhost:8000/docs (Swagger).
Response models are defined in `schemas.py`; the matching frontend types are in `rag-frontend/types/api.ts`.

| Method | Path | Description |
|---|---|---|
| `POST` | `/query/` | Ask a question (`{"question": "..."}`, max 500 characters) |
| `POST` | `/upload-pdf/` | Upload a PDF (multipart field `file`, max 20 MB) |
| `GET` | `/documents/` | List uploaded PDFs: `[{"name": "..."}]` |
| `DELETE` | `/documents/{name}` | Delete a document and its chunks (`403` unless `ALLOW_DELETE=true`) |
| `GET` | `/usage/` | Token usage of the current window |
| `GET` | `/features/` | Feature flags for the frontend: `{"allow_delete": bool}` |
| `GET` | `/static-pdfs/{name}` | Serves the uploaded PDFs (used by the preview) |

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

`status` is `success` (answer generated) or `no_result` (no chunk passed the score threshold; Gemini was not called
and `token_usage` is 0).

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
| `403` | Deletion disabled |
| `404` | Document not found |
| `409` | Duplicate upload (same content under another name, or same name with different content) |
| `413` | File larger than 20 MB |
| `422` | Empty or too long question |
| `429` | Token limit reached (the message includes the reset time) |
| `500` | Internal error (details are logged, never returned to the client) |
