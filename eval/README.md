# Retrieval evaluation

`evaluate_evidence.py` measures retrieval quality against a golden set (`golden_set.jsonl`). It is read-only: it works
on a temporary copy of the database and verifies (SHA-256) that the original is unchanged. Gemini is not called.
The embedding and reranker models must already be in the local HuggingFace cache, because the script runs offline.
Model names, collection, `retrieval_k`, score threshold and reranker settings are read from `core/settings.py`, the
same as the app; shared code is in `common.py`.

A result counts as a hit only if it is the expected file and page **and** contains the expected answer text
(evidence phrases in `tests/test_golden_set.py`). Page-level hits are reported separately for information.

```bash
# Vector search only (the app with RERANKER_ENABLED=false)
python eval/evaluate_evidence.py --k 10 --out eval/results/<name>.json

# Same flow as the app: top candidates -> score threshold -> reranker -> top retrieval_k
python eval/evaluate_evidence.py --rerank-model cross-encoder/mmarco-mMiniLMv2-L12-H384-v1 --k 10 --out eval/results/<name>.json
```

| Option | Default | |
|---|---|---|
| `--chroma-dir` | `CHROMA_DIR` from settings | Database to measure |
| `--golden` | `eval/golden_set.jsonl` | Question file |
| `--k` | `5` | Reported depth (at least 3 and `retrieval_k`) |
| `--rerank-model` | none | Cross-encoder model; omit for vector ranking |
| `--candidate-k` | `rerank_candidate_k` from settings (`10`) | Candidates passed to the reranker; `--k` must not exceed it |
| `--overwrite` | off | Replace an existing `--out` file |

"Reaches Gemini" follows the app: without the reranker, the top `retrieval_k` chunks that pass the threshold; with the
reranker, the threshold is applied to the candidates first, then the reranker order, then the top `retrieval_k`.
With a reranker the report also contains per-question rerank scores and the rerank time (mean, p95, max).

`build_normalization_db.py` is the script that built `chroma_db_normalization` (the same PDFs re-ingested with
blank-line normalization); it also checks that ingestion without normalization reproduces the original database.

## Question sets

- `golden_set.jsonl`: 56 answerable questions: `meb_yonetmelik.pdf` 25 (q01–q25), `AcilSaglikHizmetleri.pdf` 10
  (q26–q35), `isg.pdf` 10 (q36–q45), `KanMerkezleriYonetmelik.pdf` 11 (q46–q56).
- `negative_set.jsonl`: 10 questions the documents cannot answer (near-domain and out-of-domain), for calibrating the
  score threshold. They are checked by `tests/test_negative_set.py`; there is no evaluation script for them yet.

## Current results

`chroma_db_normalization` (638 chunks), 56 questions, `k=10`, measured on 2026-10-28.

| Metric | Vector search | With reranker (app default) |
|---|---|---|
| Evidence Hit@1 | 39/56 | 51/56 |
| Evidence Hit@3 | 51/56 | 55/56 |
| Evidence Hit@10 | 55/56 | 55/56 |
| Evidence MRR | 0.808 | 0.944 |
| Correct chunk reaches Gemini | 51/56 | 55/56 |
| Page Hit@3 | 54/56 | 55/56 |
| Rerank time per question (CPU, 10 candidates) | — | about 2.1–2.5 s mean |

Per file (evidence Hit@1, vector search → reranker): Acil 8/10 → 10/10, İSG 7/10 → 10/10, Kan 8/11 → 10/11,
MEB 16/25 → 21/25. The MEB row matches the earlier 25-question results.

Result files: `evidence_baseline_56q_k10.json` (vector search), `rerank_mminilm_c10_k10_prodflow.json` (app flow),
`rerank_mminilm_c10_k10.json` and `rerank_mminilm_c15_k10.json` (10 vs. 15 candidates). With 15 candidates q23 also
reaches Gemini (56/56) but reranking takes about 3.7 s. `BAAI/bge-reranker-v2-m3` was tried and dropped as too slow
on CPU.

## Limits of these numbers

- The 0.72 threshold is not calibrated: negative questions are not measured yet, and on the golden set almost all
  10 candidates pass it (9.96 on average).
- The approximate vector index can occasionally miss a near result after the database is reopened (`notes.md` item 41).
  The script then stops with "full ranking does not match evaluate_question top k" and saves nothing; run it again.
- Rerank times are from a local 8-core CPU; the Docker container is limited to 2 CPUs.
