#Baseline retrieval değerlendirmesi (salt okunur).
#Orijinal chroma_db/ klasörüne asla yazmaz: klasörü proje dışındaki geçici bir dizine kopyalar,
#sadece kopyayı açar, bitince orijinalin SHA-256 parmak izlerini tekrar karşılaştırır.
#Gemini çağrılmaz; bu yüzden core/config.py ve services/query.py import edilmez.

import argparse
import gc
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

#Model indirmeyi ve Chroma telemetrisini kütüphaneler import edilmeden önce kapatıyoruz
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["ANONYMIZED_TELEMETRY"] = "False"

#Production değerlerinin kopyası (core/config.py ve services/query.py değişirse burası da güncellenmeli)
COLLECTION_NAME = "langchain"                   #core/config.py: isim verilmediği için LangChain varsayılanı
MODEL_NAME = "intfloat/multilingual-e5-base"    #core/config.py:20
QUERY_PREFIX = "query: "                        #services/query.py:30
SCORE_THRESHOLD = 0.72                          #services/query.py:35

CONSISTENCY_SAMPLE_SIZE = 5
CONSISTENCY_MIN_COSINE = 0.999
REQUIRED_FIELDS = {"id": str, "question": str, "file": str, "pages": list}


def parse_args():
    parser = argparse.ArgumentParser(description="Read-only baseline retrieval evaluation")
    parser.add_argument("--chroma-dir", default="chroma_db")
    parser.add_argument("--golden", default="eval/golden_set.jsonl")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--out", default="eval/results/baseline_e5_noprefix.json")
    parser.add_argument("--overwrite", action="store_true", help="Replace --out if it already exists")
    args = parser.parse_args()
    if args.k < 3:
        parser.error("--k must be at least 3 to report Hit@3")
    return args


#Klasördeki her dosyanın boyutu ve SHA-256 parmak izi (sadece okuma)
def fingerprint(directory: Path) -> dict:
    prints = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            digest = hashlib.sha256()
            with open(path, "rb") as f:
                for block in iter(lambda: f.read(1024 * 1024), b""):
                    digest.update(block)
            prints[path.relative_to(directory).as_posix()] = [path.stat().st_size, digest.hexdigest()]
    return prints


def load_golden(path: Path) -> list:
    questions = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            item = json.loads(line)
            for field, field_type in REQUIRED_FIELDS.items():
                if not isinstance(item.get(field), field_type):
                    raise ValueError(f"{path}:{n}: field '{field}' missing or not {field_type.__name__}")
            questions.append(item)
    if not questions:
        raise ValueError(f"{path}: no questions found")
    return questions


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


#Kayıtlı vektörler, bu ortamda aynı modelle (ingestion gibi ön eksiz) yeniden üretilenlerle eşleşiyor mu?
#Eşleşmezse sorgular kayıtlı vektörlerden farklı bir "uzayda" olur ve baseline geçersiz sayılır
def check_consistency(db, embeddings) -> float:
    sample = db.get(limit=CONSISTENCY_SAMPLE_SIZE, include=["embeddings", "documents"])
    fresh = embeddings.embed_documents(sample["documents"])
    return min(cosine([float(x) for x in stored], new) for stored, new in zip(sample["embeddings"], fresh))


def evaluate_question(db, item: dict, k: int) -> dict:
    expected_pages = set(item["pages"])
    #Production ile aynı sorgu: services/query.py:30-31
    results = db.similarity_search_with_relevance_scores(QUERY_PREFIX + item["question"], k=k)

    retrieved = []
    for rank, (doc, score) in enumerate(results, 1):
        source = doc.metadata.get("source_file")
        page = doc.metadata.get("page_number")
        retrieved.append({
            "rank": rank,
            "source_file": source,
            "page": page,
            "score": round(float(score), 4),
            "is_hit": source == item["file"] and page in expected_pages,
        })

    #İlk isabet, isabetler arasında en yüksek skorlu olandır
    first_hit = next((r for r in retrieved if r["is_hit"]), None)
    hit_at_3 = bool(first_hit and first_hit["rank"] <= 3)
    #Baraj kontrolü production'daki gibi yuvarlanmamış skorla yapılır (yuvarlama sadece gösterim için)
    first_hit_raw_score = float(results[first_hit["rank"] - 1][1]) if first_hit else None
    return {
        "id": item["id"],
        "question": item["question"],
        "expected_file": item["file"],
        "expected_pages": item["pages"],
        "hit_at_3": hit_at_3,
        f"hit_at_{k}": first_hit is not None,
        "first_hit_rank": first_hit["rank"] if first_hit else None,
        "first_hit_score": first_hit["score"] if first_hit else None,
        #Bilgi amaçlı: production'da bu parça k=3 ve 0.72 barajından sonra Gemini'ye ulaşır mıydı?
        "reaches_llm_in_production": hit_at_3 and first_hit_raw_score >= SCORE_THRESHOLD,
        "retrieved": retrieved,
    }


def run_evaluation(copy_dir: Path, questions: list, k: int) -> dict:
    import chromadb
    import sentence_transformers
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_chroma import Chroma

    embeddings = HuggingFaceEmbeddings(model_name=MODEL_NAME, model_kwargs={"device": "cpu"})
    db = None
    try:
        db = Chroma(collection_name=COLLECTION_NAME, persist_directory=str(copy_dir), embedding_function=embeddings)

        #Koleksiyon adı yanlış olsaydı Chroma kopyada boş bir koleksiyon oluştururdu; bunu yakalıyoruz
        chunk_count = db._collection.count()
        if chunk_count == 0:
            raise RuntimeError(f"Collection '{COLLECTION_NAME}' is empty or missing in the copy")
        print(f"Collection '{COLLECTION_NAME}': {chunk_count} chunks")

        #Vektör indeksi tüm parçaları içeriyor mu? (indeks dosyaları açılışta Chroma'nın günlüğünden tamamlanıyor olabilir)
        probe = embeddings.embed_query(QUERY_PREFIX + "kontrol")
        indexed = len(db._collection.query(query_embeddings=[probe], n_results=chunk_count, include=["distances"])["ids"][0])
        print(f"Index completeness: {indexed}/{chunk_count} chunks searchable")
        if indexed != chunk_count:
            raise RuntimeError(f"Only {indexed} of {chunk_count} chunks are searchable; baseline would be incomplete")

        min_cosine = check_consistency(db, embeddings)
        print(f"Consistency check: min cosine {min_cosine:.6f} (required >= {CONSISTENCY_MIN_COSINE})")
        if min_cosine < CONSISTENCY_MIN_COSINE:
            raise RuntimeError("Stored vectors do not match this environment's model output; baseline would be invalid")

        per_question = [evaluate_question(db, item, k) for item in questions]
    finally:
        #Hata veya Ctrl+C olsa bile: Windows'ta geçici kopyanın silinebilmesi için dosya tutamaçlarını bırakıyoruz
        db = None
        gc.collect()
        chromadb.api.client.SharedSystemClient.clear_system_cache()

    n = len(per_question)
    hits_3 = sum(r["hit_at_3"] for r in per_question)
    hits_k = sum(r[f"hit_at_{k}"] for r in per_question)
    reaches_llm = sum(r["reaches_llm_in_production"] for r in per_question)
    return {
        "meta": {
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "description": "Baseline: chunks embedded without 'passage: ', queries with 'query: ' (current production)",
            "model": MODEL_NAME,
            "collection": COLLECTION_NAME,
            "chunk_count": chunk_count,
            "indexed_count": indexed,
            "query_prefix": QUERY_PREFIX,
            "score_threshold": SCORE_THRESHOLD,
            "k": k,
            "consistency_min_cosine": round(min_cosine, 6),
            "chromadb_version": chromadb.__version__,
            "sentence_transformers_version": sentence_transformers.__version__,
        },
        "summary": {
            "questions": n,
            "hit_at_3": hits_3,
            "hit_at_3_rate": round(hits_3 / n, 4),
            f"hit_at_{k}": hits_k,
            f"hit_at_{k}_rate": round(hits_k / n, 4),
            "reaches_llm_in_production": reaches_llm,
        },
        "per_question": per_question,
    }


def print_report(report: dict, k: int):
    print(f"\n{'id':<5}{'hit@3':<7}{'hit@' + str(k):<7}{'rank':<6}{'score':<8}{'llm':<5}top results (file:page score)")
    for r in report["per_question"]:
        top = " | ".join(
            f"{'*' if x['is_hit'] else ''}{(x['source_file'] or '?')[:12]}:{x['page']} {x['score']:.3f}"
            for x in r["retrieved"]
        )
        print(
            f"{r['id']:<5}{'yes' if r['hit_at_3'] else 'no':<7}{'yes' if r[f'hit_at_{k}'] else 'no':<7}"
            f"{r['first_hit_rank'] or '-':<6}{r['first_hit_score'] or '-':<8}"
            f"{'yes' if r['reaches_llm_in_production'] else 'no':<5}{top}"
        )
    s = report["summary"]
    print(f"\nHit@3: {s['hit_at_3']}/{s['questions']} ({s['hit_at_3_rate']:.0%})")
    print(f"Hit@{k}: {s[f'hit_at_{k}']}/{s['questions']} ({s[f'hit_at_{k}_rate']:.0%})")
    print(f"Correct chunk would reach Gemini in production (top 3, score >= {SCORE_THRESHOLD}): "
          f"{s['reaches_llm_in_production']}/{s['questions']}")
    print("(* = correct file and page)")


def main():
    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    original = Path(args.chroma_dir).resolve()
    golden_path = Path(args.golden).resolve()
    out_path = Path(args.out).resolve()

    if not (original / "chroma.sqlite3").is_file():
        sys.exit(f"ERROR: {original} does not look like a Chroma directory (chroma.sqlite3 missing)")
    if out_path.is_relative_to(original):
        sys.exit("ERROR: --out must not be inside the Chroma directory")
    if out_path.exists() and not args.overwrite:
        sys.exit(f"ERROR: {out_path} already exists; pass --overwrite to replace it")

    questions = load_golden(golden_path)
    print(f"Loaded {len(questions)} questions from {golden_path}")

    #1) Orijinalin parmak izi
    before = fingerprint(original)
    print(f"Fingerprinted original: {len(before)} files in {original}")

    #2) Proje dışındaki geçici klasöre kopya; değerlendirme sadece bu kopya üzerinde çalışır
    temp_root = None
    report, error, original_unchanged = None, None, False
    try:
        temp_root = Path(tempfile.mkdtemp(prefix="chroma_eval_"))
        copy_dir = temp_root / "chroma_db"
        if temp_root.resolve().is_relative_to(original):
            raise RuntimeError("Temporary copy would be inside the original directory")
        shutil.copytree(original, copy_dir)
        if fingerprint(copy_dir) != before:
            raise RuntimeError("Copy does not match the original")
        print(f"Working copy: {copy_dir}")

        report = run_evaluation(copy_dir, questions, args.k)
    except Exception as exc:
        error = exc
    finally:
        #Bu blok hata veya Ctrl+C (KeyboardInterrupt) olsa bile çalışır
        #3) Geçici kopyayı siliyoruz (orijinal değil)
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)
            if temp_root.exists():
                print(f"WARNING: could not delete temporary copy {temp_root} (outside the project, safe to delete)")

        #4) Orijinal değişmedi mi?
        original_unchanged = fingerprint(original) == before
        print(f"Original chroma_db unchanged: {original_unchanged}")

    if error is not None:
        print(f"ERROR: {error}")
        sys.exit(1)
    if not original_unchanged:
        print("ERROR: the original chroma_db changed during the run. Do not trust these results; results not saved.")
        sys.exit(1)

    report["meta"]["original_chroma_unchanged"] = original_unchanged
    report["meta"]["golden_set"] = os.path.relpath(golden_path)
    print_report(report, args.k)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
