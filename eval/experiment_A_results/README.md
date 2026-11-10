# Experiment A: article-based chunking

Goal: split the PDFs at "MADDE N" headings instead of fixed 1200/300-character windows, so an article is not cut in the
middle and every chunk carries its article number and title (`notes.md` item 13).

Every run uses the exact ranking (see [`../README.md`](../README.md#exact-ranking-and-hnsw-recall)) and the extended
question sets: 80 answerable (`golden_set.jsonl`) and 55 unanswerable (`negative_set.jsonl`).

**Result (closed on 2026-11-10): not adopted.** Article-based chunking did not beat the current `page` chunking; the app
keeps `page`. The `madde` strategy stays in `services/chunking.py` (with tests) but is not the default. See "Conclusion".

| File | Database | What |
|---|---|---|
| `baseline_evidence.json` | `chroma_db_normalization` (current, `page` chunking) | `evaluate_evidence.py`, app flow with reranker, `k=10` |
| `baseline_negative.json` | `chroma_db_normalization` | `evaluate_negative.py`, app flow |
| `madde_db_build.json`, `madde_evidence.json`, `madde_negative.json` | `chroma_db_madde` (`madde`, no overlap between parts) | build record and both evaluations |
| `madde_overlap_db_build.json`, `madde_overlap_evidence.json`, `madde_overlap_negative.json` | `chroma_db_madde_overlap` (`madde`, ~300-character overlap between parts) | build record and both evaluations |

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

## Article-based runs (2026-11-10)

`madde` strategy (`services/chunking.py`): the text is split at article headings and at page boundaries, so every chunk is
one article's part on one page; continuation parts start with the article heading ("MADDE 36 – Devam-devamsızlık ve
ilişik kesme"). Parts longer than 1200 characters are split at paragraph boundaries ("(1)", "(2)"). Text before the
first article and annexes ("EK-1"...) use the `page` method. All 80 evidence phrases stay in one chunk on their page.

| | `page` (current) | `madde` | `madde` + overlap |
|---|---|---|---|
| Chunks (mean characters) | 638 (939) | 771 (736) | 851 (736) |
| Evidence Hit@1 | **75/80** | 70/80 | 73/80 |
| Evidence Hit@3 | 79/80 | 77/80 | 79/80 |
| Evidence MRR | **0.960** | 0.919 | 0.946 |
| Correct chunk reaches Gemini | 79/80 | 77/80 | 79/80 |
| Near-domain unanswerable sent to Gemini | **8/44** | 8/44 | 9/44 |

Evidence rank per question where any run differs from rank 1 (rank / vector rank; `—` = not among the candidates):

| | `page` | `madde` | `madde` + overlap |
|---|---|---|---|
| q01 | 2 / 5 | **1** / 2 | **1** / 3 |
| q08 | 3 / 5 | 2 / 2 | 3 / 6 |
| q14 | 1 / 2 | 2 / 3 | 1 / 3 |
| q20 | 1 / 1 | 2 / 1 | 1 / 1 |
| q21 | 2 / 2 | 2 / 1 | 2 / 1 |
| q22 | 1 / 1 | 2 / 2 | 2 / 2 |
| q23 | — / 12 | — / 11 | — / 12 |
| q42 | 1 / 5 | — / 30 | 1 / 9 |
| q45 | 1 / 1 | 1 / 1 | 3 / 5 |
| q48 | 1 / 3 | — / 15 | 1 / 10 |
| q49 | 2 / 1 | 2 / 2 | 2 / 1 |
| q57 | 1 / 6 | 2 / 3 | 2 / 2 |

Unanswerable questions sent to Gemini: `page` n03, n24, n29, n30, n45, n46, n47, n50; `madde` n03, n26, n30, n45, n46,
n47, n48, n50; `madde` + overlap n03, n24, n26, n30, n32, n45, n46, n47, n50.

- **What the headings fixed:** q01 and q08 rose (2→1, 3→2) because their evidence chunk no longer starts in the middle of
  an article; their reranker scores rose from 0.32 to 1.80 and from 2.86 to 5.56.
- **What the split broke (no overlap):** q42 and q48 dropped out of the candidates. The definition of "İtiraz
  komisyonu" (`isg.pdf` Madde 4(ğ)) was cut in the middle, so its subject and its answer ended up in different chunks;
  Madde 7 of `KanMerkezleriYonetmelik.pdf` was split at paragraphs, so the chunk with "(4) Kurul, yılda en az dört
  defa..." lost the surrounding paragraphs. Smaller, focused chunks of similar articles also moved ahead (q22: "Onur
  genel kurulu ... her dönemde en az bir kez toplanır" above "Onur kurulu ... ayda en az bir kez toplanır").
- **Overlap:** the `page` method's overlap is at most 300 characters and built from whole paragraphs or lines; measured
  on the current database it averages 162 characters within a page, is 0 for a third of the chunk pairs, and never
  crosses a page. The overlap variant starts every split part with the previous part's last ~300 characters, from a
  sentence start or else a word start, within the same article and page. It brought back q42, q48, q14 and q20 and
  restored 79/80 reaching Gemini, but q45, q22 and q57 stayed below rank 1 and one more unanswerable question (n32)
  passed the thresholds.

## Conclusion

- Neither variant beats `page`; the differences are 2–5 questions out of 80, consistently in the same direction.
- The current setup was already strong: of the five questions not at rank 1, four have their evidence at rank 2–3 and
  still reach Gemini (top 3 chunks are sent). Chunk boundaries were a smaller problem than they looked; the reranker
  reads the whole chunk and usually compensates.
- Article chunks are smaller and carry less context; regulations contain similar articles, so focused chunks of the
  wrong article compete more. Article titles are mostly generic ("Tanımlar", "Görevleri") and rarely share words with
  questions.
- The one question whose evidence never reaches Gemini, q23, is a vector-search miss (evidence at vector rank 11–12, not
  among the 10 candidates) and is not affected by chunking. This points to hybrid search (Experiment B) or more
  candidates (15 candidates found it earlier, at about +50% reranking time).
- Not tried: article chunks with a larger size (~2000 characters, still under e5's 512 tokens). Given these results a
  large gain is not expected.
