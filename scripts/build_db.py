#Chroma DB'yi uploaded_pdfs/ içindeki PDF'lerden YENİ bir klasöre kurar (notes.md madde 40).
#Uygulama DB oluşturmaz (DB yoksa başlamaz); DB bu script'le bilinçli olarak kurulur. Mevcut hiçbir DB'ye yazılmaz:
#hedef klasör varsa script durur. Uygulamanın yeni DB'yi kullanması için .env'de CHROMA_DIR elle değiştirilir.
#PDF'ler uygulamanın kendi koduyla parçalanır ve yazılır (services.ingestion.prepare_chunks / process_pdf); embedding
#modeli, koleksiyon adı ve Chroma ayarları uygulamayla aynı (core/settings.py, core/config.py).
#Akış: ön kontroller → parçalama (embedding yok) → <hedef>.partial klasörüne kurulum → kopyada doğrulama → hedef adına
#taşıma → manifest. Doğrulama veya kurulum başarısız olursa .partial incelenmek üzere yerinde bırakılır.
#Varsayılan offline: model yerel HuggingFace önbelleğinde yoksa açık bir hatayla durur. İlk kurulumda --allow-download
#e5'i ve reranker'ı indirir (Docker'da script container içinde çalıştırılmalı: modeller HF_HOME volume'üne iner).
#
#--allow-empty: PDF yoksa boş DB kurulur (dokümansız ilk kurulum; PDF'ler sonra uygulamadan yüklenir). Bilinçli bir seçenek:
#yanlış klasörde çalıştırınca sessizce boş DB kurulmasın (uygulama da bu yüzden kendi kendine boş DB açmıyor)
#
#  python scripts/build_db.py --target chroma_db_yeni
#  python scripts/build_db.py --target chroma_db_yeni --allow-download   (yeni makinede ilk kurulum)
#  python scripts/build_db.py --target chroma_db_yeni --allow-empty      (dokümansız ilk kurulum)

import argparse
import gc
import hashlib
import json
import math
import os
import shutil
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

#Doğrulamada yeniden vektörize edilip kayıtlı vektörle karşılaştırılan chunk sayısı ve kabul edilen en düşük benzerlik
CONSISTENCY_SAMPLE_SIZE = 5
CONSISTENCY_MIN_COSINE = 0.999
#e5'in en uzun girdi uzunluğu (token); daha uzun chunk'ların sonu embedding'e girmez (sessizce kırpılır)
E5_MAX_TOKENS = 512


def parse_args():
    parser = argparse.ArgumentParser(description="Build a new Chroma DB from the PDFs (never overwrites an existing directory)")
    parser.add_argument("--target", required=True, help="New DB directory (relative paths are resolved from the project root)")
    parser.add_argument("--pdf-dir", help="PDF folder (default: UPLOAD_DIR from settings, uploaded_pdfs)")
    parser.add_argument("--chunking", help="Chunking strategy (default: chunking_strategy from settings)")
    parser.add_argument("--allow-download", action="store_true", help="Download missing models (e5 and reranker) from HuggingFace")
    parser.add_argument("--allow-empty", action="store_true", help="Build an empty DB when there are no PDFs (upload them later in the app)")
    parser.add_argument("--manifest", help="Build record (default: eval/results/<target name>_build.json)")
    return parser.parse_args()


#HuggingFace ayarları kütüphaneler import edilmeden önce yapılmalı (huggingface_hub değerleri import anında okuyor)
#--allow-download ortamdaki HF_HUB_OFFLINE=1'i (docker-compose.yml) de bu çalıştırma için kapatır
def configure_environment(allow_download: bool):
    os.environ["ANONYMIZED_TELEMETRY"] = "False"
    offline = "0" if allow_download else "1"
    os.environ["HF_HUB_OFFLINE"] = offline
    os.environ["TRANSFORMERS_OFFLINE"] = offline


def log(stage, message):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{stage}] {message}", flush=True)


def fmt_seconds(seconds):
    seconds = int(round(seconds))
    return f"{seconds // 60}dk {seconds % 60:02d}sn" if seconds >= 60 else f"{seconds}sn"


def resolve(path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else PROJECT_ROOT / path


#Mesajlarda ve manifest'te: proje içindeki yollar kısa (chroma_db_yeni), dışındakiler tam yol (..\..\ yerine)
def display_path(path: Path) -> str:
    path = Path(path).resolve()
    return path.relative_to(PROJECT_ROOT.resolve()).as_posix() if path.is_relative_to(PROJECT_ROOT.resolve()) else str(path)


#Klasördeki her dosyanın boyutu ve SHA-256 parmak izi (sadece okuma); mevcut DB'nin değişmediğini göstermek için
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


#Windows'ta Chroma dosyaları kapatıldıktan sonra kısa süre kilitli kalabiliyor; birkaç kez dener, olmazsa uyarır
def remove_temp_dir(path: Path, attempts: int = 5, wait_seconds: float = 1.0):
    for attempt in range(attempts):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        if attempt < attempts - 1:
            time.sleep(wait_seconds)
    log("uyarı", f"Geçici klasör silinemedi: {path} (proje dışında, elle silinebilir)")


def release_chroma():
    import chromadb
    gc.collect()
    chromadb.api.client.SharedSystemClient.clear_system_cache()


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


#Model önbellekte yoksa (offline) kütüphanenin uzun hatası yerine ne yapılacağını söyleyen bir hata
def load_model(name, loader, allow_download):
    try:
        return loader()
    except Exception as exc:
        if allow_download:
            raise
        raise RuntimeError(f"{name} yerel HuggingFace önbelleğinde bulunamadı veya açılamadı ({type(exc).__name__}). "
                           f"İlk kurulumda --allow-download ile çalıştırın.") from exc


#Embedding çağrılarını sayıp ilerleme ve tahmini kalan süreyi yazan sarmalayıcı (vektörlere dokunmaz)
class ProgressEmbeddings:
    def __init__(self, base, total):
        self.base, self.total, self.done, self.start = base, total, 0, None

    def embed_documents(self, texts):
        if self.start is None:
            self.start = time.perf_counter()
        vectors = self.base.embed_documents(texts)
        self.done += len(texts)
        elapsed = time.perf_counter() - self.start
        eta = elapsed / self.done * (self.total - self.done)
        log("3/5 kurulum", f"{self.done}/{self.total} chunk vektörize edildi | geçen {fmt_seconds(elapsed)}, tahmini kalan {fmt_seconds(eta)}")
        return vectors

    def embed_query(self, text):
        return self.base.embed_query(text)


#1/5: hedef klasör yok, PDF klasörü dolu (--allow-empty ile boş olabilir), aynı içerikli iki PDF yok
def preflight(target: Path, partial: Path, manifest_path: Path, pdf_dir: Path, ingestion, allow_empty: bool = False) -> list:
    for path, what in ((target, "Hedef klasör"), (partial, "Yarım kalmış önceki kurulum"), (manifest_path, "Manifest")):
        if path.exists():
            raise RuntimeError(f"{what} zaten var: {path}. Üzerine yazılmaz; başka bir --target seçin veya klasörü elle silin.")
    empty_hint = "Dokümansız boş bir DB için --allow-empty ile çalıştırın."
    if not pdf_dir.is_dir():
        #Uygulama UPLOAD_DIR'i açılışta kendisi oluşturuyor (main.py)
        if allow_empty:
            return []
        raise RuntimeError(f"PDF klasörü bulunamadı: {pdf_dir}. {empty_hint}")
    pdf_paths = sorted(p for p in pdf_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
    if not pdf_paths:
        if allow_empty:
            return []
        raise RuntimeError(f"PDF klasöründe PDF yok: {pdf_dir}. {empty_hint}")
    #Uygulama aynı içerikli ikinci PDF'i 409 ile reddediyor; kurulumda da iki kez eklenmemeli
    by_hash = {}
    for path in pdf_paths:
        by_hash.setdefault(ingestion.hash_pdf(str(path)), []).append(path.name)
    duplicates = [names for names in by_hash.values() if len(names) > 1]
    if duplicates:
        raise RuntimeError(f"Aynı içerikli PDF'ler var (yalnızca biri kalmalı): {duplicates}")
    return pdf_paths


def token_lengths(tokenizer, texts) -> list:
    return [len(ids) for ids in tokenizer(texts, add_special_tokens=True)["input_ids"]]


def chunk_stats(prepared: list, tokens: list) -> dict:
    lengths = sorted(len(c.page_content) for p in prepared for c in p["chunks"])
    if not lengths:
        return {"count": 0, "by_file": {}, "characters": None,
                "tokens": {"median": None, "max": None, "limit": E5_MAX_TOKENS, "over_limit": 0}}
    over = [n for n in tokens if n > E5_MAX_TOKENS]
    return {
        "count": len(lengths),
        "by_file": {p["pdf_name"]: len(p["chunks"]) for p in prepared},
        "characters": {"min": lengths[0], "median": statistics.median(lengths), "mean": round(statistics.mean(lengths), 1),
                       "max": lengths[-1]},
        "tokens": {"median": statistics.median(tokens), "max": max(tokens), "limit": E5_MAX_TOKENS, "over_limit": len(over)},
    }


#4/5: yeni DB geçici bir kopyada açılır; planlanan chunk'larla birebir aynı olmalı, aranabilir olmalı, vektörler modelle tutarlı olmalı
def verify(partial: Path, prepared: list, embeddings, collection_name: str, query_prefix: str) -> dict:
    import chromadb
    want = {i: (c.page_content, c.metadata) for p in prepared for c, i in zip(p["chunks"], p["ids"])}
    root = Path(tempfile.mkdtemp(prefix="chroma_build_verify_"))
    try:
        shutil.copytree(partial, root / "db")
        client = chromadb.PersistentClient(path=str(root / "db"))
        try:
            col = client.get_collection(collection_name)
            data = col.get(include=["documents", "metadatas", "embeddings"])
            got = {i: (d, m, e) for i, d, m, e in zip(data["ids"], data["documents"], data["metadatas"], data["embeddings"])}
            #Örnek yalnızca DB'de bulunan chunk'lardan; eksik chunk'lar ids_match / count ile ayrıca raporlanır
            present = sorted(i for i in want if i in got)
            sample = present[:: max(1, len(present) // CONSISTENCY_SAMPLE_SIZE)][:CONSISTENCY_SAMPLE_SIZE]
            fresh = embeddings.embed_documents([want[i][0] for i in sample]) if sample else []
            fields = ("source_file", "page_number", "pdf_hash")
            count = col.count()
            result = {
                "count": count,
                "ids_match": set(got) == set(want),
                "documents_match": all(i in got and got[i][0] == want[i][0] for i in want),
                "metadata_match": all(i in got and all(got[i][1].get(f) == want[i][1].get(f) for f in fields) for i in want),
                #Boş DB'de (--allow-empty) karşılaştırılacak vektör ve aranacak parça yok
                "consistency_min_cosine": round(min((cosine([float(x) for x in got[i][2]], v) for i, v in zip(sample, fresh)),
                                                    default=0.0), 6) if want else None,
                "searchable": len(col.query(query_embeddings=[embeddings.embed_query(query_prefix + "kontrol")],
                                            n_results=count)["ids"][0]) if count else 0,
                "configuration": col.configuration_json,
            }
        finally:
            client = None
            release_chroma()
    finally:
        remove_temp_dir(root)

    problems = [k for k in ("ids_match", "documents_match", "metadata_match") if not result[k]]
    if result["count"] != len(want):
        problems.append(f"count {result['count']} != {len(want)}")
    if result["searchable"] != len(want):
        problems.append(f"searchable {result['searchable']} != {len(want)}")
    if want and result["consistency_min_cosine"] < CONSISTENCY_MIN_COSINE:
        problems.append(f"consistency {result['consistency_min_cosine']} < {CONSISTENCY_MIN_COSINE}")
    #Eval ve uygulamanın skor formülü (1 - d/√2) l2 mesafesine göre; başka bir ölçüyle kurulmuş DB yanlış skor verir
    if (result["configuration"].get("hnsw") or {}).get("space") != "l2":
        problems.append(f"distance metric is not l2: {result['configuration'].get('hnsw')}")
    result["problems"] = problems
    return result


def main():
    args = parse_args()
    configure_environment(args.allow_download)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    from core.settings import get_settings
    from services import ingestion
    settings = get_settings()

    target = resolve(args.target)
    partial = target.with_name(target.name + ".partial")
    pdf_dir = resolve(args.pdf_dir) if args.pdf_dir else settings.upload_dir
    strategy = args.chunking or settings.chunking_strategy
    manifest_path = resolve(args.manifest) if args.manifest else PROJECT_ROOT / "eval" / "results" / f"{target.name}_build.json"
    app_db = settings.chroma_dir
    if strategy not in ingestion.CHUNKING_STRATEGIES:
        sys.exit(f"ERROR: bilinmeyen chunking yöntemi {strategy!r} (desteklenenler: {', '.join(ingestion.CHUNKING_STRATEGIES)})")
    if target.resolve() == app_db.resolve():
        sys.exit(f"ERROR: --target uygulamanın kullandığı DB ({app_db}); yeni bir klasör seçin")

    started = time.perf_counter()
    app_db_before = fingerprint(app_db) if app_db.exists() else None
    error, manifest, build_started = None, None, False
    try:
        log("1/5 ön kontrol", f"Hedef: {target} | PDF klasörü: {pdf_dir} | chunking: {strategy} | "
                              f"model indirme: {'açık' if args.allow_download else 'kapalı (offline)'}")
        pdf_paths = preflight(target, partial, manifest_path, pdf_dir, ingestion, args.allow_empty)
        if pdf_paths:
            log("1/5 ön kontrol", f"{len(pdf_paths)} PDF: {[p.name for p in pdf_paths]}")
            #--allow-empty yalnızca PDF yokken boş DB'ye izin verir; PDF varken DB onlardan kurulur
            if args.allow_empty:
                log("1/5 ön kontrol", "PDF bulundu; --allow-empty dikkate alınmadı, DB PDF'lerden kurulacak")
        else:
            #Modeller yine yüklenir: önbellekte olduklarını görmek için (ilk upload'da e5 gerekecek)
            log("1/5 ön kontrol", "PDF yok; --allow-empty ile boş DB kurulacak")

        #Modeller parçalamadan önce yüklenir: önbellekte yoksa uzun işlerden önce durulsun
        #Reranker DB kurulumunda kullanılmaz; yalnızca --allow-download ile indirilir (uygulama ilk açılışta offline açılabilsin)
        from langchain_huggingface import HuggingFaceEmbeddings
        from sentence_transformers import CrossEncoder
        from transformers import AutoTokenizer
        log("2/5 parçalama", f"{settings.embedding_model} yükleniyor (CPU)...")
        #core/config.py get_embeddings ile aynı ayarlar
        base_emb = load_model(settings.embedding_model, lambda: HuggingFaceEmbeddings(
            model_name=settings.embedding_model, model_kwargs={"device": "cpu"}), args.allow_download)
        tokenizer = load_model(settings.embedding_model, lambda: AutoTokenizer.from_pretrained(settings.embedding_model), args.allow_download)
        if args.allow_download:
            log("2/5 parçalama", f"{settings.reranker_model} indiriliyor / önbellekte kontrol ediliyor...")
            CrossEncoder(settings.reranker_model, device="cpu")

        #Embedding yok: chunk'lar önceden çıkarılır, sayılır ve token uzunlukları ölçülür
        prepared = []
        for path in pdf_paths:
            try:
                prepared.append(ingestion.prepare_chunks(str(path), strategy))
            except ingestion.PdfUnreadableError as exc:
                raise RuntimeError(f"{path.name}: {exc}") from exc
        all_ids = [i for p in prepared for i in p["ids"]]
        if len(set(all_ids)) != len(all_ids):
            raise RuntimeError("Chunk id'leri benzersiz değil")
        texts = [c.page_content for p in prepared for c in p["chunks"]]
        tokens = token_lengths(tokenizer, texts) if texts else []
        stats = chunk_stats(prepared, tokens)
        if stats["count"]:
            log("2/5 parçalama", f"{stats['count']} chunk {stats['by_file']} | karakter medyan {stats['characters']['median']}, "
                                 f"en uzun {stats['characters']['max']} | token medyan {stats['tokens']['median']}, en uzun {stats['tokens']['max']}")
        if stats["tokens"]["over_limit"]:
            log("uyarı", f"{stats['tokens']['over_limit']} chunk {E5_MAX_TOKENS} token sınırını aşıyor; sonları embedding'e girmez")

        #process_pdf uygulamadaki gibi chunk'ları yeniden hazırlayıp 32'şerli paketlerle yazar
        from langchain_chroma import Chroma
        build_started = True
        progress = ProgressEmbeddings(base_emb, stats["count"])
        db = Chroma(collection_name=settings.collection_name, persist_directory=str(partial), embedding_function=progress)
        embed_started = time.perf_counter()
        results = [ingestion.process_pdf(str(path), db=db, strategy=strategy) for path in pdf_paths]
        embed_seconds = time.perf_counter() - embed_started
        db = None
        release_chroma()
        if [r["total_chunks"] for r in results] != [len(p["chunks"]) for p in prepared]:
            raise RuntimeError("Yazılan chunk sayıları planlananlarla aynı değil")

        log("4/5 doğrulama", "Yeni DB geçici bir kopyada açılıp planlanan chunk'larla karşılaştırılıyor...")
        verification = verify(partial, prepared, base_emb, settings.collection_name, "query: ")
        log("4/5 doğrulama", str({k: v for k, v in verification.items() if k != "configuration"}))
        if verification["problems"]:
            raise RuntimeError(f"Doğrulama başarısız: {verification['problems']}")

        partial.rename(target)
        import chromadb
        import sentence_transformers
        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "target": display_path(target),
            "pdf_dir": display_path(pdf_dir),
            "pdfs": [{"file": p["pdf_name"], "pdf_hash": p["pdf_hash"], "pages": p["total_pages"], "chunks": len(p["chunks"])}
                     for p in prepared],
            "chunking": {"strategy": strategy, "chunk_size": settings.chunk_size, "chunk_overlap": settings.chunk_overlap},
            "model": settings.embedding_model,
            "collection": settings.collection_name,
            "chunks": stats,
            "verification": verification,
            "embedding_seconds": round(embed_seconds, 1),
            "chromadb_version": chromadb.__version__,
            "sentence_transformers_version": sentence_transformers.__version__,
        }
    except BaseException as exc:
        error = exc
    finally:
        app_db_unchanged = app_db_before is None or fingerprint(app_db) == app_db_before

    if not app_db_unchanged:
        sys.exit(f"ERROR: uygulamanın DB'si ({app_db}) çalışma sırasında değişti. Bu beklenmiyor; kontrol edin.")
    if error is not None:
        if build_started and partial.exists():
            log("son", f"Yarım kalan kurulum incelenmek üzere bırakıldı: {partial} (elle silinebilir)")
        print(f"ERROR: {error}")
        sys.exit(1)

    manifest["app_db_unchanged"] = app_db_unchanged
    manifest["total_seconds"] = round(time.perf_counter() - started, 1)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    log("5/5 son", f"Tamamlandı: {target} ({stats['count']} chunk, {fmt_seconds(manifest['total_seconds'])}). Manifest: {manifest_path}")
    if prepared:
        log("5/5 son", f"Kurulan PDF'ler: {[p['pdf_name'] for p in prepared]}")
    else:
        log("5/5 son", "Boş DB kuruldu; PDF'ler uygulamanın arayüzünden yüklenebilir")
    log("5/5 son", f"Uygulama hâlâ {display_path(app_db)} kullanıyor. Yeni DB'ye geçmek için .env'de CHROMA_DIR={display_path(target)} "
                   f"yapıp uygulamayı yeniden başlatın.")


if __name__ == "__main__":
    main()
