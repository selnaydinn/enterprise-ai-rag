#Eval script'lerinin ortak kodu (evaluate_evidence.py, build_normalization_db.py) ve tests/test_golden_set.py.
#Tek başına çalıştırılmaz. Import edildiğinde model indirmeyi ve Chroma telemetrisini kapatır,
#ayarları uygulamayla aynı yerden (core/settings.py) okur; Gemini çağrılmaz, GEMINI_API_KEY gerekmez.

import hashlib
import json
import math
import os
import shutil
import sys
import time
from pathlib import Path

#Model indirmeyi ve Chroma telemetrisini kütüphaneler import edilmeden önce kapatıyoruz
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["ANONYMIZED_TELEMETRY"] = "False"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.settings import get_settings  # noqa: E402

#Production değerleri uygulamayla aynı ayarlardan
_settings = get_settings()
CHROMA_DIR = _settings.chroma_dir
COLLECTION_NAME = _settings.collection_name
MODEL_NAME = _settings.embedding_model
SCORE_THRESHOLD = _settings.score_threshold
#Production'da Gemini'ye gönderilen en fazla parça sayısı (services/query.py search_in_db)
PRODUCTION_K = _settings.retrieval_k
#Production reranker ayarları (services/query.py rerank_chunks); --candidate-k varsayılanı buradan
RERANKER_ENABLED = _settings.reranker_enabled
RERANKER_MODEL = _settings.reranker_model
RERANK_CANDIDATE_K = _settings.rerank_candidate_k
QUERY_PREFIX = "query: "                        #services/query.py search_in_db ile aynı olmalı

CONSISTENCY_SAMPLE_SIZE = 5
CONSISTENCY_MIN_COSINE = 0.999
REQUIRED_FIELDS = {"id": str, "question": str, "file": str, "pages": list}


#Hit@3 ve production'daki retrieval_k raporlanabilsin diye k ikisinden de küçük olamaz
def check_k(parser, k: int):
    min_k = max(3, PRODUCTION_K)
    if k < min_k:
        parser.error(f"--k must be at least {min_k} to report Hit@3 and production k={PRODUCTION_K}")


#Geçici DB kopyasını siler. Windows'ta Chroma dosyaları kapatıldıktan sonra da kısa bir süre kilitli kalabiliyor;
#bu yüzden birkaç kez bekleyip tekrar deniyoruz. Yine silinemezse sessiz kalmayıp uyarıyoruz (klasör proje dışında, elle silinebilir)
def remove_temp_dir(path: Path, attempts: int = 5, wait_seconds: float = 1.0) -> bool:
    for attempt in range(attempts):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return True
        if attempt < attempts - 1:
            time.sleep(wait_seconds)
    print(f"WARNING: could not delete temporary copy {path} (outside the project, safe to delete manually)")
    return False


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
#Eşleşmezse sorgular kayıtlı vektörlerden farklı bir "uzayda" olur ve sonuçlar geçersiz sayılır
def check_consistency(db, embeddings) -> float:
    sample = db.get(limit=CONSISTENCY_SAMPLE_SIZE, include=["embeddings", "documents"])
    fresh = embeddings.embed_documents(sample["documents"])
    return min(cosine([float(x) for x in stored], new) for stored, new in zip(sample["embeddings"], fresh))


#Vektör indeksi tüm parçaları içeriyor mu? (indeks dosyaları açılışta Chroma'nın günlüğünden tamamlanıyor olabilir)
#Eksikse bazı parçalar hiç aranamaz ve sonuçlar eksik olur
def check_index_completeness(db, embeddings, chunk_count: int) -> int:
    probe = embeddings.embed_query(QUERY_PREFIX + "kontrol")
    indexed = len(db._collection.query(query_embeddings=[probe], n_results=chunk_count, include=["distances"])["ids"][0])
    print(f"Index completeness: {indexed}/{chunk_count} chunks searchable")
    if indexed != chunk_count:
        raise RuntimeError(f"Only {indexed} of {chunk_count} chunks are searchable; results would be incomplete")
    return indexed


#Sayfa düzeyi sonuç: doğru dosya + beklenen sayfa isabet sayılır (kanıt ifadesi aranmaz)
#evaluate_evidence.py ilk k sonucu bununla karşılaştırır ve sayfa alanlarını buradan alır
def evaluate_question(db, item: dict, k: int) -> dict:
    expected_pages = set(item["pages"])
    #Production ile aynı sorgu: services/query.py search_in_db
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
        #Bilgi amaçlı: production'da bu parça retrieval_k ve skor barajından sonra Gemini'ye ulaşır mıydı?
        "reaches_llm_in_production": bool(first_hit and first_hit["rank"] <= PRODUCTION_K and first_hit_raw_score >= SCORE_THRESHOLD),
        "retrieved": retrieved,
    }
