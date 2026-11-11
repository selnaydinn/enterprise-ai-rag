# Enterprise AI — Frontend

Next.js / TypeScript interface for the Enterprise AI RAG application: login, document list, chat with sources and PDF
preview.

Setup, running, the API and the overall architecture are described in the [main README](../README.md).

```bash
npm ci
npm run dev          # http://localhost:3000
npx tsc --noEmit     # type check
npm run lint         # ESLint
```

The backend address comes from `NEXT_PUBLIC_API_URL` in `.env.local` (see `.env.local.example`);
if it is not set, `http://localhost:8000` is used. Open the app at `http://localhost:3000` and keep `localhost` for the
backend too: the session cookie is not sent between `localhost` and `127.0.0.1`
([details](../docs/configuration.md#frontend-settings-rag-frontendenvlocal)).
