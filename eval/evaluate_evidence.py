#Chunk (kanıt) düzeyinde retrieval değerlendirmesi (salt okunur).
#Bir sonucun başarılı sayılması için doğru dosya + beklenen sayfa + cevap ifadesini (tests/test_golden_set.py EVIDENCE)
#içermesi gerekir; yalnızca sayfa eşleşmesi başarı sayılmaz, ayrıca "kaynak/sayfa doğruluğu" olarak raporlanır.
#Sayfa düzeyi alanlar evaluate_retrieval.evaluate_question ile birebir aynı şekilde üretilir (önceki raporlarla tutarlılık için).
#DB geçici bir kopyada açılır; hiçbir Chroma klasörüne yazılmaz.

import argparse
import hashlib
import importlib.util
import math
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

#evaluate_retrieval import edilirken HF offline ve telemetri ayarları da yapılır
import evaluate_retrieval as base  # noqa: E402

EVIDENCE_MODULE = PROJECT_ROOT / "tests" / "test_golden_set.py"


def parse_args():
    parser = argparse.ArgumentParser(description="Read-only evidence (chunk) level retrieval evaluation")
    parser.add_argument("--chroma-dir", required=True)
    parser.add_argument("--golden", default="eval/golden_set.jsonl")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if args.k < 3:
        parser.error("--k must be at least 3 to report Hit@3")
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


def evaluate(db, embeddings, item, evidence, k, chunk_count) -> dict:
    page_level = base.evaluate_question(db, item, k)
    query_vector = embeddings.embed_query(base.QUERY_PREFIX + item["question"])
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
        x["is_page_hit"] = x["source_file"] == item["file"] and x["page"] in item["pages"]
        x["is_evidence"] = is_evidence(item, evidence, x["source_file"], x["page"], x["text"])
    first_ev = next((x for x in ranked if x["is_evidence"]), None)
    top = ranked[:k]
    first_ev_top = next((x for x in top if x["is_evidence"]), None)
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
        "evidence_reaches_llm_in_production": bool(first_ev and first_ev["rank"] <= 3 and first_ev["score"] >= base.SCORE_THRESHOLD),
        #Kaynak/sayfa doğruluğu (yalnızca bilgi; başarı sayılmaz)
        "top1_correct_file": top[0]["source_file"] == item["file"],
        "top1_correct_page": top[0]["is_page_hit"],
        "page_hit_at_3": page_level["hit_at_3"],
        f"page_hit_at_{k}": page_level[f"hit_at_{k}"],
        "page_first_rank": page_level["first_hit_rank"],
        "page_hit_without_evidence_in_top3": any(x["is_page_hit"] and not x["is_evidence"] for x in top[:3]) and not any(x["is_evidence"] for x in top[:3]),
        "retrieved": [{key: (round(v, 4) if key == "score" else v) for key, v in x.items() if key != "text"} | {"text": x["text"][:300]} for x in top],
    }


def run(copy_dir: Path, questions: list, evidence: dict, k: int) -> dict:
    import chromadb
    import sentence_transformers
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_chroma import Chroma

    embeddings = HuggingFaceEmbeddings(model_name=base.MODEL_NAME, model_kwargs={"device": "cpu"})
    db = None
    try:
        db = Chroma(collection_name=base.COLLECTION_NAME, persist_directory=str(copy_dir), embedding_function=embeddings)
        chunk_count = db._collection.count()
        if chunk_count == 0:
            raise RuntimeError(f"Collection '{base.COLLECTION_NAME}' is empty or missing in the copy")
        configuration = db._collection.configuration_json
        min_cosine = base.check_consistency(db, embeddings)
        print(f"Collection '{base.COLLECTION_NAME}': {chunk_count} chunks | consistency (no prefix) min cosine {min_cosine:.6f}")
        if min_cosine < base.CONSISTENCY_MIN_COSINE:
            raise RuntimeError("Stored vectors do not match this environment's model output (no prefix)")
        per_question = [evaluate(db, embeddings, item, evidence[item["id"]], k, chunk_count) for item in questions]
    finally:
        db = None
        base.gc.collect()
        chromadb.api.client.SharedSystemClient.clear_system_cache()

    n = len(per_question)
    count = lambda key: sum(bool(r[key]) for r in per_question)
    ranks = [r["evidence_first_rank"] for r in per_question]
    return {
        "meta": {
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "description": "Evidence-level evaluation: a hit must be the expected file+page AND contain the golden evidence phrase",
            "model": base.MODEL_NAME, "collection": base.COLLECTION_NAME, "chunk_count": chunk_count,
            "hnsw": configuration["hnsw"], "query_prefix": base.QUERY_PREFIX, "passage_prefix": None,
            "score_threshold": base.SCORE_THRESHOLD, "k": k, "consistency_min_cosine": round(min_cosine, 6),
            "evidence_source": Path(os.path.relpath(EVIDENCE_MODULE)).as_posix(),
            "evidence_source_sha256": hashlib.sha256(EVIDENCE_MODULE.read_bytes()).hexdigest(),
            "chromadb_version": chromadb.__version__, "sentence_transformers_version": sentence_transformers.__version__,
        },
        "summary": {
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
        },
        "per_question": per_question,
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
    if out_path.exists():
        sys.exit(f"ERROR: {out_path} already exists; refusing to overwrite")

    questions = base.load_golden(golden_path)
    evidence = load_evidence()
    if set(evidence) != {q["id"] for q in questions}:
        sys.exit("ERROR: evidence phrases do not cover exactly the golden set ids")
    before = base.fingerprint(db_dir)
    temp_root, report, error = None, None, None
    try:
        temp_root = Path(tempfile.mkdtemp(prefix="chroma_evidence_eval_"))
        shutil.copytree(db_dir, temp_root / "db")
        if base.fingerprint(temp_root / "db") != before:
            raise RuntimeError("Copy does not match the DB")
        report = run(temp_root / "db", questions, evidence, args.k)
    except Exception as exc:
        error = exc
    finally:
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)
        unchanged = base.fingerprint(db_dir) == before
        print(f"{db_dir.name} unchanged: {unchanged}")
    if error is not None:
        print(f"ERROR: {error}")
        sys.exit(1)
    if not unchanged:
        sys.exit("ERROR: the Chroma directory changed during the run. Results not saved.")

    report["meta"]["chroma_dir"] = Path(os.path.relpath(db_dir)).as_posix()
    report["meta"]["golden_set"] = Path(os.path.relpath(golden_path)).as_posix()
    report["meta"]["db_unchanged"] = unchanged
    s = report["summary"]
    print(f"\n{'id':<5}{'kanıt sıra':>11}{'skor':>8}{'sayfa sıra':>11}  top-1 sayfa")
    for r in report["per_question"]:
        print(f"{r['id']:<5}{str(r['evidence_first_rank']):>11}{str(r['evidence_first_score']):>8}{str(r['page_first_rank']):>11}  {'doğru' if r['top1_correct_page'] else 'yanlış'}")
    print(f"\nKanıt Hit@1 {s['evidence_hit_at_1']}/{s['questions']} | Hit@3 {s['evidence_hit_at_3']} | Hit@{args.k} {s[f'evidence_hit_at_{args.k}']} | MRR {s['evidence_mrr']}")
    print(f"Sayfa Hit@3 {s['page_hit_at_3']} | Hit@{args.k} {s[f'page_hit_at_{args.k}']} | top-1 doğru sayfa {s['top1_correct_page']} | top-1 doğru dosya {s['top1_correct_file']}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        base.json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
