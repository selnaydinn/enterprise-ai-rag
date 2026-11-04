#Chunk (kanıt) düzeyinde retrieval değerlendirmesi (salt okunur).
#Bir sonucun başarılı sayılması için doğru dosya + beklenen sayfa + cevap ifadesini (tests/test_golden_set.py EVIDENCE)
#içermesi gerekir; yalnızca sayfa eşleşmesi başarı sayılmaz, ayrıca "kaynak/sayfa doğruluğu" olarak raporlanır.
#Sayfa düzeyi alanlar common.evaluate_question ile birebir aynı şekilde üretilir (önceki raporlarla tutarlılık için).
#DB geçici bir kopyada açılır; hiçbir Chroma klasörüne yazılmaz.
#Retrieval ölçümünün tek giriş noktası budur; ortak fonksiyonlar eval/common.py'de.
#--rerank-model verilirse: e5 ile ilk --candidate-k aday çekilir, cross-encoder ile yeniden sıralanır ve
#tüm metrikler yeni sıraya göre hesaplanır (aday listesine girmeyen kanıt ıskalama sayılır).

import argparse
import hashlib
import json
import importlib.util
import math
import os
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

#common import edilirken HF offline ve telemetri ayarları da yapılır
import common  # noqa: E402
from services.query import rerank_scores  # noqa: E402

EVIDENCE_MODULE = PROJECT_ROOT / "tests" / "test_golden_set.py"


def parse_args():
    parser = argparse.ArgumentParser(description="Read-only evidence (chunk) level retrieval evaluation")
    #Varsayılan DB uygulamanınkiyle aynı (core/settings.py chroma_dir)
    parser.add_argument("--chroma-dir", default=str(common.CHROMA_DIR))
    parser.add_argument("--golden", default="eval/golden_set.jsonl")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--out", required=True)
    parser.add_argument("--overwrite", action="store_true", help="Replace --out if it already exists")
    #Reranker (cross-encoder) modeli yerel HuggingFace önbelleğinde olmalı (eval offline çalışır)
    #Production ile aynı ölçüm için: --rerank-model <core/settings.py reranker_model> (--candidate-k varsayılanı production'dan)
    parser.add_argument("--rerank-model", help=f"e.g. {common.RERANKER_MODEL} (omit for plain vector ranking)")
    parser.add_argument("--candidate-k", type=int, default=common.RERANK_CANDIDATE_K, help="Candidates passed to the reranker")
    args = parser.parse_args()
    common.check_k(parser, args.k)
    #Reranker yalnızca aday listesini sıralar; raporlanan derinlik aday sayısını aşamaz
    if args.rerank_model and args.k > args.candidate_k:
        parser.error(f"--k ({args.k}) must not exceed --candidate-k ({args.candidate_k}) when reranking")
    return args


def load_evidence():
    spec = importlib.util.spec_from_file_location("golden_evidence", EVIDENCE_MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.EVIDENCE


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


#Sayfa başına kanıt (dict) varsa o sayfanın ifadesi, yoksa tek ifade aranır; beklenen sayfa dışındaki chunk asla kanıt sayılmaz
def is_evidence(item, evidence, source, page, text) -> bool:
    if source != item["file"] or page not in item["pages"]:
        return False
    phrase = evidence.get(page) if isinstance(evidence, dict) else evidence
    return phrase is not None and squash(phrase) in squash(text)


#Soru vektörü bir kez hesaplanır: evaluate_question (LangChain içinden) ve tam sıralama aynı vektörü kullanır
#İki ayrı hesaplama CPU'daki paralel toplama yüzünden son hanelerde farklı çıkıp same_top kontrolünü bozabiliyor
class CachedQueryEmbeddings:
    def __init__(self, inner):
        self.inner = inner
        self.cache = {}

    def embed_documents(self, texts):
        return self.inner.embed_documents(texts)

    def embed_query(self, text):
        if text not in self.cache:
            self.cache[text] = self.inner.embed_query(text)
        return self.cache[text]


#Adayları cross-encoder skoruna göre sıralar; eşit skorda vektör sırası korunur (sorted kararlıdır)
#Skorlar production'daki fonksiyonla alınır (services/query.py rerank_scores; soru ön eksiz verilir)
def rerank(reranker, question, candidates) -> tuple:
    start = time.perf_counter()
    scores = rerank_scores(reranker, question, [x["text"] for x in candidates])
    seconds = time.perf_counter() - start
    for x, s in zip(candidates, scores):
        x["rerank_score"] = s
    reordered = sorted(candidates, key=lambda x: -x["rerank_score"])
    for rank, x in enumerate(reordered, 1):
        x["rank"] = rank
    return reordered, seconds


def evaluate(db, embeddings, item, evidence, k, chunk_count, reranker=None, candidate_k=None) -> dict:
    page_level = common.evaluate_question(db, item, k)
    query_vector = embeddings.embed_query(common.QUERY_PREFIX + item["question"])
    #Tüm koleksiyon sıralaması: ilk k evaluate_question ile aynı olmalı; cevap chunk'ının tam sırası buradan gelir
    full = db._collection.query(query_embeddings=[query_vector], n_results=chunk_count, include=["documents", "metadatas", "distances"])
    ranked = [
        {"rank": r, "id": cid, "source_file": m.get("source_file"), "page": m.get("page_number"),
         "score": 1.0 - d / math.sqrt(2), "text": doc}
        for r, (cid, doc, m, d) in enumerate(zip(full["ids"][0], full["documents"][0], full["metadatas"][0], full["distances"][0]), 1)
    ]
    same_top = [(x["source_file"], x["page"], round(x["score"], 4)) for x in ranked[:k]] == \
        [(x["source_file"], x["page"], x["score"]) for x in page_level["retrieved"]]
    if not same_top:
        raise RuntimeError(f"{item['id']}: full ranking does not match evaluate_question top {k}")

    for x in ranked:
        x["vector_rank"] = x["rank"]
        x["is_page_hit"] = x["source_file"] == item["file"] and x["page"] in item["pages"]
        x["is_evidence"] = is_evidence(item, evidence, x["source_file"], x["page"], x["text"])
    vector_first_ev = next((x for x in ranked if x["is_evidence"]), None)

    #Reranker yoksa sıra tüm koleksiyonun vektör sırasıdır; varsa yalnızca ilk candidate_k aday yeniden sıralanır
    rerank_seconds = None
    final = ranked
    if reranker is not None:
        final, rerank_seconds = rerank(reranker, item["question"], [dict(x) for x in ranked[:candidate_k]])

    first_ev = next((x for x in final if x["is_evidence"]), None)
    top = final[:k]
    first_ev_top = next((x for x in top if x["is_evidence"]), None)
    #Sayfa alanları son sıradan hesaplanır; reranker yokken evaluate_question ile aynıdır (same_top kontrolü)
    first_page_top = next((x for x in top if x["is_page_hit"]), None)
    #Gemini'ye giden parçalar production akışıyla (services/query.py prepare_chunks_with_metadata):
    #reranker yokken ilk retrieval_k aday barajdan geçirilir; varken önce e5 barajı, sonra reranker barajı (en iyi skor),
    #sonra reranker sırası, sonra ilk retrieval_k
    #Skorlar soru-parça başına bağımsız ve sıralama kararlı olduğu için yeniden sıralanmış listeyi süzmek aynı sonucu verir
    if reranker is None:
        llm_chunks = [x for x in ranked[:common.PRODUCTION_K] if x["score"] >= common.SCORE_THRESHOLD]
    else:
        llm_chunks = [x for x in final if x["score"] >= common.SCORE_THRESHOLD]
        #Soru düzeyinde reranker barajı (services/query.py rerank_chunks)
        if llm_chunks and max(x["rerank_score"] for x in llm_chunks) < common.RERANK_SCORE_THRESHOLD:
            llm_chunks = []
        llm_chunks = llm_chunks[:common.PRODUCTION_K]
    rerank_fields = {}
    if reranker is not None:
        non_evidence = [x["rerank_score"] for x in final if not x["is_evidence"]]
        rerank_fields = {
            "evidence_vector_rank": vector_first_ev["rank"] if vector_first_ev else None,
            "evidence_rerank_score": round(first_ev["rerank_score"], 4) if first_ev else None,
            "best_non_evidence_rerank_score": round(max(non_evidence), 4) if non_evidence else None,
            "rerank_seconds": round(rerank_seconds, 4),
        }
    return {
        "id": item["id"],
        "question": item["question"],
        "expected_file": item["file"],
        "expected_pages": item["pages"],
        #Kanıt düzeyi (başarı ölçütü)
        "evidence_hit_at_1": bool(first_ev and first_ev["rank"] == 1),
        "evidence_hit_at_3": bool(first_ev and first_ev["rank"] <= 3),
        f"evidence_hit_at_{k}": bool(first_ev_top),
        "evidence_first_rank": first_ev["rank"] if first_ev else None,
        "evidence_first_score": round(first_ev["score"], 4) if first_ev else None,
        "evidence_first_chunk": first_ev["id"] if first_ev else None,
        "evidence_chunks_in_collection": sum(x["is_evidence"] for x in ranked),
        #Baraj e5 skoruna uygulanır (reranker varken de e5 barajı ön filtre olarak kalır)
        "evidence_reaches_llm_in_production": any(x["is_evidence"] for x in llm_chunks),
        "chunks_sent_to_llm": len(llm_chunks),
        **rerank_fields,
        #Kaynak/sayfa doğruluğu (yalnızca bilgi; başarı sayılmaz)
        "top1_correct_file": top[0]["source_file"] == item["file"],
        "top1_correct_page": top[0]["is_page_hit"],
        "page_hit_at_3": any(x["is_page_hit"] for x in top[:3]),
        f"page_hit_at_{k}": first_page_top is not None,
        "page_first_rank": first_page_top["rank"] if first_page_top else None,
        "page_hit_without_evidence_in_top3": any(x["is_page_hit"] and not x["is_evidence"] for x in top[:3]) and not any(x["is_evidence"] for x in top[:3]),
        "retrieved": [{key: (round(v, 4) if key in ("score", "rerank_score") else v) for key, v in x.items() if key != "text"} | {"text": x["text"][:300]} for x in top],
    }


def run(copy_dir: Path, questions: list, evidence: dict, k: int, rerank_model=None, candidate_k=None) -> dict:
    import chromadb
    import sentence_transformers
    from langchain_huggingface import HuggingFaceEmbeddings

    embeddings = CachedQueryEmbeddings(HuggingFaceEmbeddings(model_name=common.MODEL_NAME, model_kwargs={"device": "cpu"}))
    reranker = None
    if rerank_model:
        reranker = sentence_transformers.CrossEncoder(rerank_model, device="cpu")
        #İlk çağrı (ısınma) yavaş olur; süre ölçümünü bozmasın diye ölçümden önce bir kez çalıştırıyoruz
        reranker.predict([("ısınma", "ısınma")])
    db = None
    try:
        db, db_info = common.open_checked_db(copy_dir, embeddings)
        chunk_count = db_info["chunk_count"]
        if reranker is not None and candidate_k > chunk_count:
            raise RuntimeError(f"--candidate-k ({candidate_k}) exceeds the collection size ({chunk_count})")
        per_question = [evaluate(db, embeddings, item, evidence[item["id"]], k, chunk_count, reranker, candidate_k) for item in questions]
    finally:
        db = None
        common.close_db()

    summary = summarize(per_question, k)
    summary["per_file"] = {
        name: summarize([r for r in per_question if r["expected_file"] == name], k)
        for name in sorted({r["expected_file"] for r in per_question})
    }
    if reranker is not None:
        seconds = sorted(r["rerank_seconds"] for r in per_question)
        summary["rerank_seconds_mean"] = round(statistics.mean(seconds), 4)
        summary["rerank_seconds_p95"] = round(seconds[min(len(seconds) - 1, math.ceil(0.95 * len(seconds)) - 1)], 4)
        summary["rerank_seconds_max"] = round(seconds[-1], 4)
    return {
        "meta": {
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "description": "Evidence-level evaluation: a hit must be the expected file+page AND contain the golden evidence phrase",
            "rerank_model": rerank_model, "candidate_k": candidate_k if rerank_model else None,
            "model": common.MODEL_NAME, "collection": common.COLLECTION_NAME, "chunk_count": chunk_count, "indexed_count": db_info["indexed_count"],
            "hnsw": db_info["hnsw"], "query_prefix": common.QUERY_PREFIX, "passage_prefix": None,
            "score_threshold": common.SCORE_THRESHOLD, "production_k": common.PRODUCTION_K, "k": k,
            #Ölçülen kurulumun production ayarlarıyla (core/settings.py) aynı olup olmadığı görülebilsin
            "production_reranker": {"enabled": common.RERANKER_ENABLED, "model": common.RERANKER_MODEL, "candidate_k": common.RERANK_CANDIDATE_K},
            "consistency_min_cosine": db_info["consistency_min_cosine"],
            "evidence_source": Path(os.path.relpath(EVIDENCE_MODULE)).as_posix(),
            "evidence_source_sha256": hashlib.sha256(EVIDENCE_MODULE.read_bytes()).hexdigest(),
            "chromadb_version": chromadb.__version__, "sentence_transformers_version": sentence_transformers.__version__,
        },
        "summary": summary,
        "per_question": per_question,
    }


#Genel ve dosya bazında özet aynı alanlarla üretilir
#meb_yonetmelik.pdf satırı (q01-q25) eski 25 soruluk sonuçlarla kıyaslanabilir
def summarize(rows: list, k: int) -> dict:
    n = len(rows)
    def count(key):
        return sum(bool(r[key]) for r in rows)
    ranks = [r["evidence_first_rank"] for r in rows]
    return {
        "questions": n,
        "evidence_hit_at_1": count("evidence_hit_at_1"),
        "evidence_hit_at_3": count("evidence_hit_at_3"),
        f"evidence_hit_at_{k}": count(f"evidence_hit_at_{k}"),
        "evidence_reaches_llm_in_production": count("evidence_reaches_llm_in_production"),
        "evidence_mrr": round(sum(1 / r for r in ranks if r) / n, 4),
        "evidence_rank_median": sorted(r or 10**6 for r in ranks)[n // 2],
        "questions_without_evidence_chunk": sum(r is None for r in ranks),
        "top1_correct_file": count("top1_correct_file"),
        "top1_correct_page": count("top1_correct_page"),
        "page_hit_at_3": count("page_hit_at_3"),
        f"page_hit_at_{k}": count(f"page_hit_at_{k}"),
        "page_hit_without_evidence_in_top3": count("page_hit_without_evidence_in_top3"),
    }


def main():
    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    db_dir = Path(args.chroma_dir).resolve()
    golden_path = Path(args.golden).resolve()
    out_path = Path(args.out).resolve()
    if not (db_dir / "chroma.sqlite3").is_file():
        sys.exit(f"ERROR: {db_dir} does not look like a Chroma directory")
    if out_path.is_relative_to(db_dir):
        sys.exit("ERROR: --out must not be inside the Chroma directory")
    if out_path.exists() and not args.overwrite:
        sys.exit(f"ERROR: {out_path} already exists; pass --overwrite to replace it")

    questions = common.load_golden(golden_path)
    evidence = load_evidence()
    if set(evidence) != {q["id"] for q in questions}:
        sys.exit("ERROR: evidence phrases do not cover exactly the golden set ids")
    #Hata olursa veya DB değişirse run_on_copy sonucu kaydetmeden çıkar
    report = common.run_on_copy(db_dir, "chroma_evidence_eval_",
                                lambda copy_dir: run(copy_dir, questions, evidence, args.k, args.rerank_model, args.candidate_k))

    report["meta"]["chroma_dir"] = Path(os.path.relpath(db_dir)).as_posix()
    report["meta"]["golden_set"] = Path(os.path.relpath(golden_path)).as_posix()
    report["meta"]["db_unchanged"] = True
    s = report["summary"]
    reranked = args.rerank_model is not None
    #Reranker varken kanıtın vektör sırası (önce) ve reranker sırası (sonra) yan yana gösterilir
    print(f"\n{'id':<5}{'kanıt sıra':>11}{'skor':>8}{'sayfa sıra':>11}  top-1 sayfa" + (f"{'vektör sıra':>13}{'rerank skor':>13}" if reranked else ""))
    for r in report["per_question"]:
        line = f"{r['id']:<5}{str(r['evidence_first_rank']):>11}{str(r['evidence_first_score']):>8}{str(r['page_first_rank']):>11}  {'doğru' if r['top1_correct_page'] else 'yanlış':<6}"
        if reranked:
            line += f"{str(r['evidence_vector_rank']):>13}{str(r['evidence_rerank_score']):>13}"
        print(line)
    print(f"\nKanıt Hit@1 {s['evidence_hit_at_1']}/{s['questions']} | Hit@3 {s['evidence_hit_at_3']} | Hit@{args.k} {s[f'evidence_hit_at_{args.k}']} | MRR {s['evidence_mrr']}")
    print(f"Sayfa Hit@3 {s['page_hit_at_3']} | Hit@{args.k} {s[f'page_hit_at_{args.k}']} | top-1 doğru sayfa {s['top1_correct_page']} | top-1 doğru dosya {s['top1_correct_file']}")
    flow = "baraj → rerank → ilk" if reranked else "ilk"
    print(f"Gemini'ye ulaşan kanıt (skor >= {common.SCORE_THRESHOLD}, {flow} {common.PRODUCTION_K}): {s['evidence_reaches_llm_in_production']}/{s['questions']}")
    if reranked:
        print(f"Reranker: {args.rerank_model} | aday {args.candidate_k} | soru başına süre ort {s['rerank_seconds_mean']} sn · p95 {s['rerank_seconds_p95']} sn · max {s['rerank_seconds_max']} sn")
    print("\nDosya bazında (kanıt Hit@1 · Hit@3 · MRR · top-1 yanlış dosya):")
    for name, fs in s["per_file"].items():
        n = fs["questions"]
        print(f"  {name:<30}{fs['evidence_hit_at_1']}/{n} · {fs['evidence_hit_at_3']}/{n} · {fs['evidence_mrr']} · {n - fs['top1_correct_file']}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
