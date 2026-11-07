# Retrieval evaluation

`evaluate_evidence.py` measures retrieval quality against a golden set (`golden_set.jsonl`). It is read-only: it works
on a temporary copy of the database and verifies (SHA-256) that the original is unchanged. Gemini is not called.
The embedding and reranker models must already be in the local HuggingFace cache, because the script runs offline.
Model names, collection, `retrieval_k`, score threshold and reranker settings are read from `core/settings.py`, the
same as the app; shared code is in `common.py`.

A result counts as a hit only if it is the expected file and page **and** contains the expected answer text
(evidence phrases in `tests/test_golden_set.py`). Page-level hits are reported separately for information.

All metrics come from an exact ranking (every stored vector compared with the question), so a run gives the same
numbers every time. The app's approximate search is measured next to it; see "Exact ranking and HNSW recall".

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
| `--recall-depth` | the app's candidate depth | Depth for HNSW recall (see below); does not change the metrics |
| `--overwrite` | off | Replace an existing `--out` file |

"Reaches Gemini" follows the app: without the reranker, the top `retrieval_k` chunks that pass the threshold; with the
reranker, the threshold is applied to the candidates first, then the reranker order, then the top `retrieval_k`.
The flow is defined once (`production_llm_chunks` in `evaluate_evidence.py`) and used by both scripts.
With a reranker the report also contains per-question rerank scores and the rerank time (mean, p95, max).

## Exact ranking and HNSW recall

The app searches Chroma's HNSW index, which is approximate and is rebuilt in memory every time the database is opened.
Some openings miss a chunk that is really among the nearest ones (`notes.md` item 41). If the evaluation used that
search, numbers could change between runs and a change (e.g. chunking) could not be told apart from index luck.

So both scripts (`evaluate_evidence.py` and `evaluate_negative.py`):

1. Load every chunk with its vector once (`common.load_vectors`; stops if counts, vectors or metadata are inconsistent).
2. Rank all chunks by exact distance for each question (`common.exact_ranking`). Distance and score are the same as
   Chroma's `l2` and LangChain's `1 - d/√2`, so scores are on the app's scale and the thresholds stay valid.
   **All metrics, candidates and threshold sweeps come from this ranking.**
3. Run the app's HNSW search with the same question vector, at the depth the app uses (`--candidate-k` with the
   reranker, `retrieval_k` without; `--recall-depth` can ask for more).
4. Check that each chunk HNSW returns has the same distance as in the exact ranking (`common.check_distance_agreement`,
   tolerance 1e-4; measured difference about 1e-7). A mismatch stops the run: it would mean a different distance metric.
5. Report what HNSW missed (`common.recall_at_k`), without changing the metrics:
   - per question: `hnsw_recall` (`k`, `found`, `recall`, missed chunks with their exact rank), and whether the result
     would differ with HNSW (`evidence_reaches_llm_hnsw`; in `evaluate_negative.py` `sends_to_gemini_hnsw` and
     `hnsw_changes_outcome`, the miss that matters for unanswerable questions);
   - in the summary: mean recall, questions with a miss, missed evidence; the console lists each miss as `UYARI`.

A run never stops because HNSW missed something. The meta block records `ranking: "exact"`, both depths and the largest
distance difference.

Databases are built with `scripts/build_db.py` (into a new directory; `--chroma-dir` then measures it). It rebuilds
`chroma_db_normalization` exactly (checked on 2026-11-07: same ids, texts and metadata, same results). That database
was originally built by `eval/build_normalization_db.py` (blank-line normalization experiment), removed since.

## Question sets

- `golden_set.jsonl`: 56 answerable questions: `meb_yonetmelik.pdf` 25 (q01–q25), `AcilSaglikHizmetleri.pdf` 10
  (q26–q35), `isg.pdf` 10 (q36–q45), `KanMerkezleriYonetmelik.pdf` 11 (q46–q56).
- `negative_set.jsonl`: 39 questions the documents cannot answer, for calibrating the score threshold: 28 near-domain
  (close to a document's topic, often with a misleading phrase; 7–8 per document) and 11 out-of-domain. n19 was
  removed: `isg.pdf` p.10 says the fee is "Bakanlıkça belirlenen belge veya vize bedeli", a partial answer; its id is
  not reused.
  They are checked by `tests/test_negative_set.py`.

## Score thresholds

```bash
python eval/evaluate_negative.py --out eval/results/<name>.json
```

Runs the golden and negative questions through the app's retrieval (top 10 of the exact ranking, reranker scores) and
records the best e5 score and the best reranker score per question. It prints how many unanswerable questions would reach Gemini
and how many answerable questions would be rejected for a range of thresholds; it does not choose a threshold.

| Best score per question | Answerable (lowest) | Near-domain unanswerable (highest) |
|---|---|---|
| e5 | 0.768 | 0.804 |
| Reranker | −1.78 (q16) | −1.24 (n03) |

The e5 scores overlap widely, the reranker scores only slightly. The app therefore keeps e5 ≥ 0.72 as a coarse filter
(it blocks all 11 out-of-domain questions and no answerable one) and adds `rerank_score_threshold = -2.0`:

| Reaches Gemini | e5 ≥ 0.72 only | + reranker ≥ −2.0 (app default) |
|---|---|---|
| Near-domain unanswerable | 28/28 | 4/28 (n03, n24, n29, n30) |
| Out-of-domain unanswerable | 0/11 | 0/11 |
| Answerable | 56/56 | 56/56 |

Result files: `negative_mminilm_c10_gate_39neg_exact.json` (app flow, current set, exact ranking, 2026-11-06; the
reference). `negative_mminilm_c10_gate_39neg.json` is the same run made earlier with HNSW: in that run HNSW missed a
candidate for n08, n09 and n10. Only n10 matters: its best reranker score is −3.36, not −7.64, so the highest
out-of-domain reranker score is −3.36 (not −3.49) and the reranker sweep at −5.0 to −3.5 lets one more out-of-domain
question through. The flow above (4/28, 0/11, 56/56) and the −2.0 threshold are unaffected. `negative_mminilm_c10.json`
(before the reranker threshold) and `negative_mminilm_c10_gate.json` still include n19 (40 questions); the threshold was
chosen on those runs. For the questions that do reach Gemini, the prompt asks it to reply `BILGI_YOK` when the chunks contain
nothing relevant; the app then returns the same `no_result` response (checked by hand for n29).
The margin is small (q16 at −1.78), and the thresholds were chosen on the questions they are reported on, so new
questions should be added before tightening them. Reranker scores are raw model outputs; re-measure if the model changes.

## Current results

`chroma_db_normalization` (638 chunks), 56 questions, `k=10`, measured on 2026-10-28 with HNSW and again on 2026-11-06
with the exact ranking: every field of every question was identical (in those runs HNSW missed nothing).

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

HNSW recall@10 (2026-11-06, one opening): answerable 56/56 questions complete; near-domain unanswerable 27/28 (n04
missed the chunk ranked 2nd, which passes e5 0.72; the reranker threshold rejects the question either way);
out-of-domain 10/11 (n40, 6th). No miss changed whether a question reaches Gemini. Misses vary between openings.

## Limits of these numbers

- The e5 threshold (0.72) lets almost all 10 candidates of answerable questions through (9.96 on average); the
  decision whether a question is answerable is made by the reranker threshold (see "Score thresholds").
- The metrics describe the exact ranking. The app uses HNSW, which can miss a near chunk after the database is
  reopened (`notes.md` item 41); how often, and whether it changes the outcome, is in the HNSW recall fields. The
  recall depth is the app's candidate depth; misses further down are not visible unless `--recall-depth` is raised.
- Scores are computed in float64 from float32 vectors and can differ from Chroma's in the last digits (about 1e-7);
  unrounded `top_e5` / `evidence_e5` values in older result files differ by that much.
- Rerank times are from a local 8-core CPU; the Docker container is limited to 2 CPUs.
