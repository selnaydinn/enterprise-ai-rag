#Eval script'lerinin ortak kodu (evaluate_evidence.py, build_normalization_db.py) ve tests/test_golden_set.py.
#Tek başına çalıştırılmaz. Import edildiğinde model indirmeyi ve Chroma telemetrisini kapatır,
#ayarları uygulamayla aynı yerden (core/settings.py) okur; Gemini çağrılmaz, GEMINI_API_KEY gerekmez.

import gc
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

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
RERANK_SCORE_THRESHOLD = _settings.rerank_score_threshold
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


#DB'yi geçici bir kopyada açıp fn(kopya_klasörü)'nü çalıştırır; kopya parmak iziyle doğrulanır, sonunda silinir
#Hata olursa veya orijinal klasör değiştiyse sonuç kaydedilmeden çıkılır
def run_on_copy(db_dir: Path, prefix: str, fn):
    before = fingerprint(db_dir)
    temp_root, result, error = None, None, None
    try:
        temp_root = Path(tempfile.mkdtemp(prefix=prefix))
        shutil.copytree(db_dir, temp_root / "db")
        if fingerprint(temp_root / "db") != before:
            raise RuntimeError("Copy does not match the DB")
        result = fn(temp_root / "db")
    except Exception as exc:
        error = exc
    finally:
        if temp_root is not None:
            remove_temp_dir(temp_root)
        unchanged = fingerprint(db_dir) == before
        print(f"{db_dir.name} unchanged: {unchanged}")
    if error is not None:
        print(f"ERROR: {error}")
        sys.exit(1)
    if not unchanged:
        sys.exit("ERROR: the Chroma directory changed during the run. Results not saved.")
    return result


#Kopyadaki koleksiyonu açar ve ölçümden önce kontrol eder: boş değil, tüm parçalar aranabilir,
#kayıtlı vektörler bu ortamdaki modelle tutarlı. DB nesnesi ve meta'ya yazılacak bilgiler döner
def open_checked_db(copy_dir: Path, embeddings):
    from langchain_chroma import Chroma
    db = Chroma(collection_name=COLLECTION_NAME, persist_directory=str(copy_dir), embedding_function=embeddings)
    chunk_count = db._collection.count()
    if chunk_count == 0:
        raise RuntimeError(f"Collection '{COLLECTION_NAME}' is empty or missing in the copy")
    indexed = check_index_completeness(db, embeddings, chunk_count)
    min_cosine = check_consistency(db, embeddings)
    print(f"Collection '{COLLECTION_NAME}': {chunk_count} chunks | consistency (no prefix) min cosine {min_cosine:.6f}")
    if min_cosine < CONSISTENCY_MIN_COSINE:
        raise RuntimeError("Stored vectors do not match this environment's model output (no prefix)")
    info = {"chunk_count": chunk_count, "indexed_count": indexed,
            "hnsw": db._collection.configuration_json["hnsw"], "consistency_min_cosine": round(min_cosine, 6)}
    return db, info


#Açık Chroma bağlantılarını bırakır; çağıran taraf DB nesnesine referansı önce bırakmalı (db = None)
def close_db():
    import chromadb
    gc.collect()
    chromadb.api.client.SharedSystemClient.clear_system_cache()


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


#Tam sıralama için koleksiyondaki tüm parçalar (eval'de referans; production HNSW kullanıyor, notes.md madde 41)
#ids, vectors (N x boyut, float32), documents ve metadatas aynı sırada; sıra id'ye göre sabit
@dataclass(frozen=True)
class CollectionVectors:
    ids: list
    vectors: np.ndarray
    documents: list
    metadatas: list


#Metadata'da bulunması gereken alanlar (ingestion.py process_pdf yazıyor; eval sayfa ve kanıtı bunlarla eşleştiriyor)
REQUIRED_METADATA = ("source_file", "page_number")


#Tüm parçaları vektörleriyle bir kez alır ve ölçümden önce kontrol eder; bir sorun varsa RuntimeError (sonuç kaydedilmez)
#Chroma'nın get() sırası garanti değil; eşit mesafede hangi parçanın önde olacağı buna bağlı olmasın diye id'ye göre sıralanır
def load_vectors(db, chunk_count: int) -> CollectionVectors:
    data = db._collection.get(include=["embeddings", "documents", "metadatas"])
    ids = list(data["ids"])
    embeddings, documents, metadatas = data["embeddings"], data["documents"], data["metadatas"]

    if len(ids) != chunk_count:
        raise RuntimeError(f"get() returned {len(ids)} chunks, collection count is {chunk_count}")
    if embeddings is None or len(embeddings) != len(ids):
        raise RuntimeError("get() did not return one embedding per chunk")
    if documents is None or len(documents) != len(ids) or metadatas is None or len(metadatas) != len(ids):
        raise RuntimeError("get() did not return one document and one metadata per chunk")
    if len(set(ids)) != len(ids):
        raise RuntimeError("Duplicate chunk ids in the collection")

    #Boyutlar farklıysa np.asarray tek bir matris kuramaz; önce tek tek bakıyoruz
    dims = {len(v) for v in embeddings}
    if len(dims) != 1 or 0 in dims:
        raise RuntimeError(f"Embeddings have inconsistent or empty dimensions: {sorted(dims)}")
    vectors = np.asarray(embeddings, dtype=np.float32)
    if not np.isfinite(vectors).all():
        raise RuntimeError("Some embeddings contain NaN or infinite values")
    if not (np.linalg.norm(vectors, axis=1) > 0).all():
        raise RuntimeError("Some embeddings are zero vectors")

    for cid, doc, meta in zip(ids, documents, metadatas):
        if not isinstance(doc, str) or not doc:
            raise RuntimeError(f"Chunk {cid} has no text")
        missing = [f for f in REQUIRED_METADATA if not meta or meta.get(f) is None]
        if missing:
            raise RuntimeError(f"Chunk {cid} is missing metadata: {', '.join(missing)}")

    order = sorted(range(len(ids)), key=lambda i: ids[i])
    return CollectionVectors(
        ids=[ids[i] for i in order],
        vectors=vectors[order],
        documents=[documents[i] for i in order],
        metadatas=[metadatas[i] for i in order],
    )


#Soru vektörünü koleksiyondaki tüm parçalarla karşılaştırıp hepsini sıralı döner (HNSW yok, her çalıştırmada aynı sonuç)
#Mesafe Chroma'nın "l2" tanımıyla aynı: karesi alınmış öklid (chromadb/utils/distance_functions.py l2)
#Skor LangChain'in l2 için kullandığı 1 - d/√2 (production'daki similarity_search_with_relevance_scores ile aynı ölçek)
#Hesap float64'te; vektörler float32 saklandığı için Chroma'nın mesafeleriyle son hanelerde fark olabilir
#Eşit mesafede id sırası korunur (load_vectors id'ye göre sıralıyor, argsort kararlı)
def exact_ranking(collection: CollectionVectors, query_vector) -> list:
    query = np.asarray(query_vector, dtype=np.float64)
    if query.shape != (collection.vectors.shape[1],):
        raise RuntimeError(f"Query vector has shape {query.shape}, collection vectors have {collection.vectors.shape[1]} dimensions")
    if not np.isfinite(query).all():
        raise RuntimeError("Query vector contains NaN or infinite values")

    distances = ((collection.vectors.astype(np.float64) - query) ** 2).sum(axis=1)
    order = np.argsort(distances, kind="stable")
    return [
        {
            "rank": rank,
            "id": collection.ids[i],
            "text": collection.documents[i],
            "metadata": collection.metadatas[i],
            "distance": float(distances[i]),
            "score": 1.0 - float(distances[i]) / math.sqrt(2),
        }
        for rank, i in enumerate(order, 1)
    ]


#Production'daki aramanın eval karşılığı (services/query.py search_in_db → LangChain similarity_search_with_relevance_scores):
#LangChain de koleksiyona aynı query() çağrısını yapıp skoru 1 - d/√2 ile hesaplıyor. Soru vektörü dışarıdan verilir:
#exact_ranking ile aynı vektör kullanılsın (aynı metnin iki ayrı embedding'i CPU'da son hanelerde farklı çıkabiliyor)
#Çıktı exact_ranking ile aynı biçimde; HNSW yaklaşık olduğu için parça kaçırabilir (recall_at_k ile ölçülür)
def hnsw_ranking(db, query_vector, k: int) -> list:
    if not isinstance(k, int) or isinstance(k, bool) or k < 1:
        raise RuntimeError(f"k must be a positive integer, got {k!r}")
    query = [float(x) for x in query_vector]
    if not all(math.isfinite(x) for x in query):
        raise RuntimeError("Query vector contains NaN or infinite values")

    result = db._collection.query(query_embeddings=[query], n_results=k, include=["documents", "metadatas", "distances"])
    ids, documents = result["ids"][0], result["documents"][0]
    metadatas, distances = result["metadatas"][0], result["distances"][0]
    if not (len(ids) == len(documents) == len(metadatas) == len(distances)):
        raise RuntimeError("query() returned fields of different lengths")
    if len(ids) > k:
        raise RuntimeError(f"query() returned {len(ids)} chunks for k={k}")
    if len(set(ids)) != len(ids):
        raise RuntimeError("query() returned duplicate chunk ids")
    return [
        {
            "rank": rank,
            "id": cid,
            "text": doc,
            "metadata": meta,
            "distance": float(d),
            "score": 1.0 - float(d) / math.sqrt(2),
        }
        for rank, (cid, doc, meta, d) in enumerate(zip(ids, documents, metadatas, distances), 1)
    ]


#exact_ranking ile Chroma'nın mesafeleri arasında kabul edilen en büyük fark
#float32 kaynaklı fark ~1e-7 civarında; formül farkı (ör. DB "cosine" ile kurulduysa) çok daha büyük olur
DISTANCE_TOLERANCE = 1e-4


#HNSW'nin döndürdüğü her parçanın mesafesi, aynı parçanın exact_ranking'deki mesafesiyle eşleşiyor mu?
#Bu kontrol numpy formülünün Chroma'nın hesabıyla aynı olduğunu her çalıştırmada doğrular; mesafe ölçüsü değişirse
#eval yanlış sonuç üretmek yerine durur. HNSW'nin kaçırdığı parçalar burada aranmaz (recall_at_k'nın işi)
#En büyük farkı döner (meta'ya yazılır; gerçek float32 farkı böylece görülür)
def check_distance_agreement(exact: list, hnsw: list, tolerance: float = DISTANCE_TOLERANCE) -> float:
    exact_by_id = {r["id"]: r for r in exact}
    max_diff = 0.0
    for r in hnsw:
        reference = exact_by_id.get(r["id"])
        if reference is None:
            raise RuntimeError(f"HNSW returned chunk {r['id']} that is not in the collection vectors")
        diff = abs(r["distance"] - reference["distance"])
        if not diff <= tolerance:
            raise RuntimeError(f"Distance mismatch for chunk {r['id']}: HNSW {r['distance']:.8f}, "
                               f"exact {reference['distance']:.8f} (difference {diff:.2e} > {tolerance:.0e})")
        max_diff = max(max_diff, diff)
    return max_diff


#HNSW, tam sıralamanın ilk k parçasının kaçını kendi ilk k'sında buldu? (k: production'ın aday derinliği)
#k koleksiyondan büyükse koleksiyon boyutuna iner; koleksiyon boşsa kaçırılacak parça yoktur (recall 1.0)
#Sıra farkı recall'u etkilemez, yalnızca kaçırılan parçalar sayılır; kaçırılanlar tam sıradaki yerleriyle döner
def recall_at_k(exact: list, hnsw: list, k: int) -> dict:
    if not isinstance(k, int) or isinstance(k, bool) or k < 1:
        raise RuntimeError(f"k must be a positive integer, got {k!r}")
    depth = min(k, len(exact))
    found_ids = {r["id"] for r in hnsw[:depth]}
    missed = [r for r in exact[:depth] if r["id"] not in found_ids]
    found = depth - len(missed)
    return {
        "k": depth,
        "found": found,
        "recall": found / depth if depth else 1.0,
        "missed": [{"id": r["id"], "exact_rank": r["rank"], "score": round(r["score"], 4)} for r in missed],
    }


#Sayfa düzeyi sonuç: doğru dosya + beklenen sayfa isabet sayılır (kanıt ifadesi aranmaz)
#Arama yapmaz; hazır sıralamanın (exact_ranking veya hnsw_ranking çıktısı) ilk k parçasına bakar
#Böylece aynı metrikler tam sıralamadan da HNSW sonucundan da hesaplanabilir; çıktı alanları önceki raporlarla aynı
def evaluate_question(item: dict, ranked: list, k: int) -> dict:
    expected_pages = set(item["pages"])
    results = ranked[:k]

    retrieved = []
    for rank, r in enumerate(results, 1):
        source = r["metadata"].get("source_file")
        page = r["metadata"].get("page_number")
        retrieved.append({
            "rank": rank,
            "source_file": source,
            "page": page,
            "score": round(float(r["score"]), 4),
            "is_hit": source == item["file"] and page in expected_pages,
        })

    #İlk isabet, isabetler arasında en yüksek skorlu olandır
    first_hit = next((r for r in retrieved if r["is_hit"]), None)
    hit_at_3 = bool(first_hit and first_hit["rank"] <= 3)
    #Baraj kontrolü production'daki gibi yuvarlanmamış skorla yapılır (yuvarlama sadece gösterim için)
    first_hit_raw_score = float(results[first_hit["rank"] - 1]["score"]) if first_hit else None
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
