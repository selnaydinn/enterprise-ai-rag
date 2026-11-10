# Experiment A: article-based chunking

Goal: split the PDFs at "MADDE N" headings instead of fixed 1200/300-character windows, so an article is not cut in the
middle and every chunk carries its article number and title (`notes.md` item 13).

Every run uses the exact ranking (see [`../README.md`](../README.md#exact-ranking-and-hnsw-recall)) and the extended
question sets: 80 answerable (`golden_set.jsonl`) and 55 unanswerable (`negative_set.jsonl`).

| File | Database | What |
|---|---|---|
| `baseline_evidence.json` | `chroma_db_normalization` (current, `page` chunking) | `evaluate_evidence.py`, app flow with reranker, `k=10` |
| `baseline_negative.json` | `chroma_db_normalization` | `evaluate_negative.py`, app flow |

The baseline is measured with the current thresholds unchanged (e5 0.72, reranker −2.0): it shows how the current setup
does on the 24 answerable and 16 unanswerable questions added on 2026-11-10, and it is the reference for the
article-based database.

## Baseline (2026-11-10)

| | All | First 56 / 39 | Added (q57–q80 / n41–n56) |
|---|---|---|---|
| Evidence Hit@1 | 75/80 | 51/56 | 24/24 |
| Evidence Hit@3 | 79/80 | 55/56 | 24/24 |
| Evidence MRR | 0.960 | 0.944 | 1.0 |
| Correct chunk reaches Gemini | 79/80 | 55/56 | 24/24 |
| Near-domain unanswerable sent to Gemini | 8/44 | 4/28 | 4/16 |
| Out-of-domain unanswerable sent to Gemini | 0/11 | 0/11 | — |

- **Answerable:** all 24 added questions are found at rank 1 by the reranker, although they were written to be harder
  (paraphrased, distractor on the same page, answer inside a list). The questions that miss rank 1 are the same five as
  before: q01, q08, q21, q49 (evidence at rank 2–3) and q23 (evidence at vector rank 12, never a candidate).
- **Unanswerable:** 4 of the 16 added near-domain questions pass both thresholds, all with a positive best reranker score:
  n46 (1.12), n50 (0.66), n47 (0.59), n45 (0.28). Their best chunks are related but do not answer them: `isg.pdf` p.5
  (OSGB service durations, Madde 11–12) for n45–n47, and `KanMerkezleriYonetmelik.pdf` p.9 (licence application form)
  for n50, matched on "başvuru". These scores are above several answerable questions (q16 −1.78, q43 −0.53, q21 −0.32,
  q20 0.06), so no reranker threshold separates them without losing answerable questions. For these, the second line of
  defence is Gemini replying `BILGI_YOK`. Checked by hand in the app on 2026-11-10 for the 8 near-domain questions that
  pass (n03, n24, n29, n30, n45, n46, n47, n50): 6 got `BILGI_YOK`; n46 and n03 (both OSGB, `isg.pdf`) got an answer
  saying the documents contain no direct information, with related details; none got an invented answer. One manual
  run; Gemini's answers can vary.
- The −2.0 threshold was chosen on the first 39 unanswerable questions; on new questions it lets through 25% (4/16)
  instead of 14% (4/28).
- Reranking took 3.2 s per question on average in this run (2.3–2.5 s in earlier runs on the same machine).
- HNSW recall@10 in this opening: answerable 79/80 complete (q21 missed its 3rd chunk again, not the evidence), 42/44
  near-domain, 9/11 out-of-domain; no miss changed whether a question reaches Gemini.
