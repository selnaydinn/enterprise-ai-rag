# Retrieval evaluation

These scripts measure retrieval quality against a golden set (`golden_set.jsonl`). They are read-only: they work on
a temporary copy of the database and verify (SHA-256) that the original is unchanged. Gemini is not called.
The embedding model must already be in the local HuggingFace cache, because the scripts run offline.
Model name, collection and score threshold are read from `core/settings.py`, the same as the app.

```bash
# Page level: is the expected page among the top-k results?
python eval/evaluate_retrieval.py --chroma-dir chroma_db_normalization --out eval/results/<name>.json

# Evidence level: does the result also contain the expected answer text?
python eval/evaluate_evidence.py --chroma-dir chroma_db_normalization --out eval/results/<name>.json
```

Both scripts accept `--golden` (default `eval/golden_set.jsonl`) and `--k` (default `5`).
`evaluate_retrieval.py` refuses to overwrite an existing `--out` file unless `--overwrite` is given.

`build_normalization_db.py` is the script that built `chroma_db_normalization` (the same PDFs re-ingested with
blank-line normalization); it also checks that ingestion without normalization reproduces the original database.

## Current results

`chroma_db_normalization`, 25 questions, `k=5`. First measured on 2026-10-18; unchanged after the LangChain package
migration and the settings refactor (2026-10-26).

| Metric | Result |
|---|---|
| Page Hit@3 | 24/25 (96%) |
| Page Hit@5 | 25/25 (100%) |
| Correct chunk reaches Gemini in production (top 3, score ≥ 0.72) | 24/25 |
| Evidence Hit@1 / Hit@3 / Hit@5 | 16/25 · 22/25 · 24/25 |
| Evidence MRR | 0.766 |

Result files are in `results/` (e.g. `normalization_e5_noprefix_25q.json`, `evidence_normalization_25q.json`).

## Limits of these numbers

- All 25 questions come from a single document (`meb_yonetmelik.pdf`); the other PDFs and cross-document
  confusion are not measured.
- There are no questions without an answer (negative questions), so the 0.72 score threshold is not calibrated.
