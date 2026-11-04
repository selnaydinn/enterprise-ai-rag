#Skor barajı kalibrasyonu için ölçüm (salt okunur). Gemini çağrılmaz.
#Golden set (cevabı olan) ve negatif set (cevabı olmayan) soruları production akışıyla aranır:
#e5 ile ilk --candidate-k aday ("query:" ön ekiyle), her aday için reranker skoru (services/query.py rerank_scores).
#Her soru için iki sinyal kaydedilir: en yüksek e5 skoru ve ilk --candidate-k aday içindeki en yüksek reranker skoru.
#Eşik taraması her eşik değeri için kaç negatif sorunun Gemini'ye gideceğini ve kaç cevaplı sorunun kaybedileceğini gösterir.
#Script karar vermez; eşik seçimi sonuç tablosuna bakılarak yapılır. DB geçici bir kopyada açılır (common.run_on_copy).

import argparse
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

#common import edilirken HF offline ve telemetri ayarları da yapılır
import common  # noqa: E402
from evaluate_evidence import is_evidence, load_evidence  # noqa: E402
from services.query import rerank_scores  # noqa: E402

GROUPS = ("golden", "near_domain", "out_of_domain")
NEGATIVE_FIELDS = {"id": str, "kind": str, "question": str}
#Tarama aralıkları: e5 skorları dar bir aralıkta, reranker skorları ham logit (eval sonuçlarında yaklaşık -6 ile 11 arası)
E5_THRESHOLDS = [round(0.70 + i * 0.01, 2) for i in range(21)]
RERANK_THRESHOLDS = [round(-5.0 + i * 0.5, 1) for i in range(27)]


def parse_args():
    parser = argparse.ArgumentParser(description="Read-only score distributions of answerable vs. unanswerable questions")
    parser.add_argument("--chroma-dir", default=str(common.CHROMA_DIR))
    parser.add_argument("--golden", default="eval/golden_set.jsonl")
    parser.add_argument("--negative", default="eval/negative_set.jsonl")
    parser.add_argument("--rerank-model", default=common.RERANKER_MODEL)
    parser.add_argument("--candidate-k", type=int, default=common.RERANK_CANDIDATE_K)
    parser.add_argument("--out", required=True)
    parser.add_argument("--overwrite", action="store_true", help="Replace --out if it already exists")
    args = parser.parse_args()
    if args.candidate_k < common.PRODUCTION_K:
        parser.error(f"--candidate-k must be at least retrieval_k ({common.PRODUCTION_K})")
    return args


def load_negative(path: Path) -> list:
    items = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            item = json.loads(line)
            for field, field_type in NEGATIVE_FIELDS.items():
                if not isinstance(item.get(field), field_type):
                    raise ValueError(f"{path}:{n}: field '{field}' missing or not {field_type.__name__}")
            if item["kind"] not in GROUPS[1:]:
                raise ValueError(f"{path}:{n}: unknown kind '{item['kind']}'")
            items.append(item)
    if not items:
        raise ValueError(f"{path}: no questions found")
    return items


#Bir sorunun adayları, iki sinyali ve bugünkü production akışının sonucu
#evidence None ise soru negatiftir (kanıt aranmaz)
def measure(db, reranker, item: dict, group: str, candidate_k: int, evidence=None) -> dict:
    results = db.similarity_search_with_relevance_scores(common.QUERY_PREFIX + item["question"], k=candidate_k)
    rerank = rerank_scores(reranker, item["question"], [doc.page_content for doc, _ in results])
    candidates = []
    for (doc, score), r in zip(results, rerank):
        source, page = doc.metadata.get("source_file"), doc.metadata.get("page_number")
        candidates.append({
            "source_file": source, "page": page, "e5": float(score), "rerank": r,
            "is_evidence": evidence is not None and is_evidence(item, evidence, source, page, doc.page_content),
        })

    #Bugünkü akış (services/query.py prepare_chunks_with_metadata): e5 barajı, reranker barajı (en iyi skor), reranker sırası, ilk retrieval_k
    passed = [c for c in candidates if c["e5"] >= common.SCORE_THRESHOLD]
    if common.RERANKER_ENABLED:
        if passed and max(c["rerank"] for c in passed) < common.RERANK_SCORE_THRESHOLD:
            passed = []
        passed = sorted(passed, key=lambda c: -c["rerank"])
    sent = passed[:common.PRODUCTION_K]
    evidence_chunks = [c for c in candidates if c["is_evidence"]]
    return {
        "id": item["id"],
        "group": group,
        "question": item["question"],
        "file": item.get("file"),
        "top_e5": max(c["e5"] for c in candidates),
        "top_rerank": max(c["rerank"] for c in candidates),
        "candidates_passing_e5_threshold": sum(c["e5"] >= common.SCORE_THRESHOLD for c in candidates),
        #Kanıt parçalarının en iyi değerleri; kanıt adaylar arasında yoksa None
        "evidence_e5": max((c["e5"] for c in evidence_chunks), default=None),
        "evidence_rerank": max((c["rerank"] for c in evidence_chunks), default=None),
        "sends_to_gemini": bool(sent),
        "chunks_sent": len(sent),
        "evidence_reaches_gemini": any(c["is_evidence"] for c in sent) if evidence is not None else None,
        "candidates": [{**c, "e5": round(c["e5"], 4), "rerank": round(c["rerank"], 4)} for c in candidates],
    }


#Soru düzeyinde baraj: sorunun en iyi sinyali eşiğin altındaysa Gemini çağrılmaz
#Her eşik için: Gemini'ye giden negatif sorular (yanlış kabul), cevapsız kalan golden sorular (yanlış red),
#kanıtı eşiğin altında kalan veya adaylarda olmayan golden sorular (parça başına uygulanırsa kanıt elenir)
def sweep(rows: list, signal: str, thresholds: list) -> list:
    top, chunk = f"top_{signal}", f"evidence_{signal}"
    golden = [r for r in rows if r["group"] == "golden"]
    table = []
    for t in thresholds:
        table.append({
            "threshold": t,
            "near_domain_accepted": sum(r[top] >= t for r in rows if r["group"] == "near_domain"),
            "out_of_domain_accepted": sum(r[top] >= t for r in rows if r["group"] == "out_of_domain"),
            "golden_rejected": sum(r[top] < t for r in golden),
            "golden_evidence_below": sum(r[chunk] is None or r[chunk] < t for r in golden),
        })
    return table


def describe(values: list) -> dict:
    return {"n": len(values), "min": round(min(values), 4), "median": round(statistics.median(values), 4), "max": round(max(values), 4)}


def summarize(rows: list) -> dict:
    by_group = {g: [r for r in rows if r["group"] == g] for g in GROUPS}
    golden = by_group["golden"]
    return {
        "questions": {g: len(v) for g, v in by_group.items()},
        "distributions": {
            signal: {g: describe([r[f"top_{signal}"] for r in v]) for g, v in by_group.items() if v}
            for signal in ("e5", "rerank")
        },
        "evidence_distributions": {
            signal: describe([r[f"evidence_{signal}"] for r in golden if r[f"evidence_{signal}"] is not None])
            for signal in ("e5", "rerank")
        },
        "golden_without_evidence_in_candidates": sum(r["evidence_e5"] is None for r in golden),
        #Bugünkü akışta (e5 barajı → reranker barajı → reranker sırası → ilk retrieval_k) Gemini'ye giden sorular
        "current_flow": {
            "negatives_sent_to_gemini": {g: sum(r["sends_to_gemini"] for r in by_group[g]) for g in GROUPS[1:]},
            "golden_sent_to_gemini": sum(r["sends_to_gemini"] for r in golden),
            "golden_evidence_reaches_gemini": sum(bool(r["evidence_reaches_gemini"]) for r in golden),
        },
    }


def run(copy_dir: Path, golden: list, negative: list, evidence: dict, rerank_model: str, candidate_k: int) -> dict:
    import chromadb
    import sentence_transformers
    from langchain_huggingface import HuggingFaceEmbeddings

    embeddings = HuggingFaceEmbeddings(model_name=common.MODEL_NAME, model_kwargs={"device": "cpu"})
    reranker = sentence_transformers.CrossEncoder(rerank_model, device="cpu")
    db = None
    try:
        db, db_info = common.open_checked_db(copy_dir, embeddings)
        if candidate_k > db_info["chunk_count"]:
            raise RuntimeError(f"--candidate-k ({candidate_k}) exceeds the collection size ({db_info['chunk_count']})")
        rows = [measure(db, reranker, item, "golden", candidate_k, evidence[item["id"]]) for item in golden]
        rows += [measure(db, reranker, item, item["kind"], candidate_k) for item in negative]
    finally:
        db = None
        common.close_db()

    return {
        "meta": {
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "description": "Top e5 and top reranker score per question, answerable (golden) vs. unanswerable (negative) questions",
            "model": common.MODEL_NAME, "rerank_model": rerank_model, "candidate_k": candidate_k,
            "collection": common.COLLECTION_NAME, **db_info, "query_prefix": common.QUERY_PREFIX,
            "score_threshold": common.SCORE_THRESHOLD, "rerank_score_threshold": common.RERANK_SCORE_THRESHOLD, "production_k": common.PRODUCTION_K,
            "production_reranker": {"enabled": common.RERANKER_ENABLED, "model": common.RERANKER_MODEL, "candidate_k": common.RERANK_CANDIDATE_K},
            "chromadb_version": chromadb.__version__, "sentence_transformers_version": sentence_transformers.__version__,
        },
        "summary": summarize(rows),
        "sweeps": {"e5": sweep(rows, "e5", E5_THRESHOLDS), "rerank": sweep(rows, "rerank", RERANK_THRESHOLDS)},
        "per_question": rows,
    }


def print_report(report: dict):
    s = report["summary"]
    q = s["questions"]
    print(f"\nSorular: golden {q['golden']} | dokümana yakın negatif {q['near_domain']} | kapsam dışı negatif {q['out_of_domain']}")
    c = s["current_flow"]
    print(f"Bugünkü akışta Gemini'ye giden: dokümana yakın {c['negatives_sent_to_gemini']['near_domain']}/{q['near_domain']} | "
          f"kapsam dışı {c['negatives_sent_to_gemini']['out_of_domain']}/{q['out_of_domain']} | "
          f"golden {c['golden_sent_to_gemini']}/{q['golden']} (kanıt ulaşan {c['golden_evidence_reaches_gemini']})")
    for signal in ("e5", "rerank"):
        print(f"\nEn yüksek {signal} skoru (min · medyan · max):")
        for g, d in s["distributions"][signal].items():
            print(f"  {g:<15}{d['min']:>9}{d['median']:>9}{d['max']:>9}")
        e = s["evidence_distributions"][signal]
        print(f"  {'golden kanıt':<15}{e['min']:>9}{e['median']:>9}{e['max']:>9}")
    for signal, rows in report["sweeps"].items():
        print(f"\nEşik taraması ({signal}; soru düzeyinde: en iyi skor eşiğin altındaysa Gemini çağrılmaz)")
        print(f"  {'eşik':>6}{'yakın neg. geçen':>18}{'kapsam dışı geçen':>19}{'golden reddedilen':>19}{'kanıt eşik altı':>17}")
        for r in rows:
            print(f"  {r['threshold']:>6}{r['near_domain_accepted']:>18}{r['out_of_domain_accepted']:>19}{r['golden_rejected']:>19}{r['golden_evidence_below']:>17}")


def main():
    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    db_dir = Path(args.chroma_dir).resolve()
    golden_path, negative_path, out_path = Path(args.golden).resolve(), Path(args.negative).resolve(), Path(args.out).resolve()
    if not (db_dir / "chroma.sqlite3").is_file():
        sys.exit(f"ERROR: {db_dir} does not look like a Chroma directory")
    if out_path.is_relative_to(db_dir):
        sys.exit("ERROR: --out must not be inside the Chroma directory")
    if out_path.exists() and not args.overwrite:
        sys.exit(f"ERROR: {out_path} already exists; pass --overwrite to replace it")

    golden = common.load_golden(golden_path)
    negative = load_negative(negative_path)
    evidence = load_evidence()
    if set(evidence) != {q["id"] for q in golden}:
        sys.exit("ERROR: evidence phrases do not cover exactly the golden set ids")
    report = common.run_on_copy(db_dir, "chroma_negative_eval_",
                                lambda copy_dir: run(copy_dir, golden, negative, evidence, args.rerank_model, args.candidate_k))

    report["meta"]["chroma_dir"] = Path(os.path.relpath(db_dir)).as_posix()
    report["meta"]["golden_set"] = Path(os.path.relpath(golden_path)).as_posix()
    report["meta"]["negative_set"] = Path(os.path.relpath(negative_path)).as_posix()
    report["meta"]["db_unchanged"] = True
    print_report(report)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
