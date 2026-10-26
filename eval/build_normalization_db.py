#Boş satır normalizasyonu deneyi için AYRI bir Chroma veritabanı oluşturur.
#PDF'ler mevcut ingestion akışından (services.ingestion.process_pdf) geçer; yalnızca modüldeki db yeni klasöre yönlendirilir.
#Referans chroma_db/ asla açılmaz: geçici bir kopyası okunur ve bitince parmak izleri tekrar karşılaştırılır.
#Kontrol adımı: aynı process_pdf normalizasyon KAPALIYKEN sahte bir DB ile çalıştırılır ve chroma_db ile birebir aynı
#chunk'ları üretmesi beklenir. Böylece yeni DB ile baseline arasındaki tek farkın normalizasyon olduğu doğrulanır.

import argparse
import contextlib
import gc
import importlib
import io
import json
import os
import re
import shutil
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

#Model indirmeyi ve Chroma telemetrisini kütüphaneler import edilmeden önce kapatıyoruz
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["ANONYMIZED_TELEMETRY"] = "False"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from evaluate_retrieval import COLLECTION_NAME, MODEL_NAME, cosine, fingerprint  # noqa: E402

CONSISTENCY_SAMPLE_SIZE = 5


def parse_args():
    parser = argparse.ArgumentParser(description="Build a separate Chroma DB through process_pdf with blank-line normalization")
    parser.add_argument("--pdf-dir", default="uploaded_pdfs")
    parser.add_argument("--reference", default="chroma_db", help="Baseline DB; only a temporary copy is read")
    parser.add_argument("--also-unchanged", default="chroma_db_passage", help="Only fingerprinted, never opened")
    parser.add_argument("--target", default="chroma_db_normalization")
    parser.add_argument("--manifest", default="eval/results/normalization_db_build.json")
    return parser.parse_args()


def log(stage, message):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{stage}] {message}", flush=True)


def fmt_seconds(seconds):
    seconds = int(round(seconds))
    return f"{seconds // 60}dk {seconds % 60:02d}sn" if seconds >= 60 else f"{seconds}sn"


def release_chroma():
    import chromadb
    gc.collect()
    chromadb.api.client.SharedSystemClient.clear_system_cache()


#core/config.py model/DB/Gemini'yi import anında yüklemiyor; DB'ye run_process_pdf'te get_db yerine verilen db kullanılıyor
def import_ingestion():
    return importlib.import_module("services.ingestion")


#Hiçbir şey yazmayan sahte DB: process_pdf'in add_documents'a verdiği chunk'ları ve id'leri toplar
class CaptureDB:
    def __init__(self):
        self.ids, self.documents, self.metadatas = [], [], []

    def get(self, where=None, limit=None):
        return {"ids": []}

    def add_documents(self, documents, ids):
        self.ids.extend(ids)
        self.documents.extend(d.page_content for d in documents)
        self.metadatas.extend(dict(d.metadata) for d in documents)


def run_process_pdf(ingestion, db, pdf_paths, normalize=True):
    #process_pdf artık logging kullanıyor; stdout'u yutmak yalnızca olası print'lere karşı, akışın kendisi değişmiyor
    patches = [] if normalize else [("normalize_blank_lines", lambda text: text)]
    originals = {name: getattr(ingestion, name) for name, _ in patches}
    original_get_db = ingestion.get_db
    try:
        for name, value in patches:
            setattr(ingestion, name, value)
        ingestion.get_db = lambda: db
        results = []
        for path in pdf_paths:
            with contextlib.redirect_stdout(io.StringIO()):
                results.append(ingestion.process_pdf(str(path)))
        return results
    finally:
        ingestion.get_db = original_get_db
        for name, value in originals.items():
            setattr(ingestion, name, value)


def read_reference(copy_dir: Path) -> dict:
    import chromadb
    client = chromadb.PersistentClient(path=str(copy_dir))
    try:
        collection = client.get_collection(COLLECTION_NAME)
        data = collection.get(include=["documents", "metadatas"])
        return {"ids": list(data["ids"]), "documents": list(data["documents"]),
                "metadatas": [dict(m) for m in data["metadatas"]], "configuration": collection.configuration_json}
    finally:
        client = None
        release_chroma()


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
        log("3/4 embedding", f"{self.done}/{self.total} chunk tamamlandı, {self.total - self.done} kaldı | geçen {fmt_seconds(elapsed)}, tahmini kalan {fmt_seconds(eta)}")
        return vectors

    def embed_query(self, text):
        return self.base.embed_query(text)


#Her chunk'ın sayfa metninde nerede bittiğini bulup bölmenin hangi ayraçta yapıldığını sınıflandırıyoruz
def boundary_stats(page_texts: dict, captured: CaptureDB) -> dict:
    kinds = {"paragraf (boş satır)": 0, "satır sonu": 0, "boşluk/kelime": 0, "sayfa sonu": 0, "bulunamadı": 0}
    cursor = {}
    for text, meta in zip(captured.documents, captured.metadatas):
        key = (meta["source_file"], meta["page"])
        page = page_texts[key]
        pos = page.find(text, cursor.get(key, 0))
        if pos < 0:
            kinds["bulunamadı"] += 1
            continue
        cursor[key] = pos + 1
        rest = page[pos + len(text):]
        if not rest.strip():
            kinds["sayfa sonu"] += 1
        elif re.match(r"[^\S\n]*\n[^\S\n]*\n", rest):
            kinds["paragraf (boş satır)"] += 1
        elif re.match(r"[^\S\n]*\n", rest):
            kinds["satır sonu"] += 1
        else:
            kinds["boşluk/kelime"] += 1
    return kinds


def chunk_stats(captured: CaptureDB, page_texts: dict) -> dict:
    lengths = sorted(len(d) for d in captured.documents)
    pct = lambda p: lengths[min(len(lengths) - 1, int(p * len(lengths)))]
    by_file = {}
    for meta in captured.metadatas:
        by_file[meta["source_file"]] = by_file.get(meta["source_file"], 0) + 1
    return {
        "count": len(lengths),
        "by_file": dict(sorted(by_file.items())),
        "length": {"min": lengths[0], "p10": pct(0.1), "median": statistics.median(lengths), "mean": round(statistics.mean(lengths), 1),
                   "p90": pct(0.9), "max": lengths[-1], "under_300": sum(n < 300 for n in lengths), "under_600": sum(n < 600 for n in lengths)},
        "starts_lowercase": sum(bool(re.match(r"\s*[a-zçğıöşü]", d)) and not re.match(r"\s*[a-zçğıöşü]\)", d) for d in captured.documents),
        "ends_with_sentence_punct": sum(bool(re.search(r"[.:;]\s*$", d)) for d in captured.documents),
        "split_at": boundary_stats(page_texts, captured),
    }


def main():
    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    pdf_dir = Path(args.pdf_dir).resolve()
    reference = Path(args.reference).resolve()
    also = Path(args.also_unchanged).resolve()
    target = Path(args.target).resolve()
    partial = target.with_name(target.name + ".partial")
    manifest_path = Path(args.manifest).resolve()

    if not (reference / "chroma.sqlite3").is_file():
        sys.exit(f"ERROR: {reference} does not look like a Chroma directory")
    for path in (target, partial, manifest_path):
        if path.exists():
            sys.exit(f"ERROR: {path} already exists; refusing to overwrite")

    started = time.perf_counter()
    log("1/4 hazırlık", "Referans parmak izleri alınıyor")
    before_ref = fingerprint(reference)
    before_also = fingerprint(also) if also.exists() else None

    temp_root, error, manifest = None, None, {}
    try:
        temp_root = Path(tempfile.mkdtemp(prefix="chroma_norm_ref_"))
        copy_dir = temp_root / "chroma_db"
        shutil.copytree(reference, copy_dir)
        if fingerprint(copy_dir) != before_ref:
            raise RuntimeError("Reference copy does not match the original")
        ref = read_reference(copy_dir)
        pdf_names = sorted({m["source_file"] for m in ref["metadatas"]})
        pdf_paths = [pdf_dir / name for name in pdf_names]
        missing = [str(p) for p in pdf_paths if not p.is_file()]
        if missing:
            raise RuntimeError(f"PDFs referenced by the baseline are missing: {missing}")
        extra = sorted(p.name for p in pdf_dir.glob("*.pdf") if p.name not in pdf_names)
        log("1/4 hazırlık", f"Baseline'daki PDF'ler: {pdf_names} ({len(ref['ids'])} chunk); baseline'da olmayan PDF: {extra or 'yok'}")

        ingestion = import_ingestion()
        from langchain_community.document_loaders import PyPDFLoader
        raw_pages, norm_pages = {}, {}
        for path in pdf_paths:
            for page in PyPDFLoader(str(path)).load():
                raw_pages[(path.name, page.metadata["page"])] = page.page_content
                norm_pages[(path.name, page.metadata["page"])] = ingestion.normalize_blank_lines(page.page_content)

        #Kontrol: normalizasyon kapalıyken process_pdf baseline'ı birebir üretmeli
        control = CaptureDB()
        run_process_pdf(ingestion, control, pdf_paths, normalize=False)
        ref_by_id = dict(zip(ref["ids"], ref["documents"]))
        control_match = control.ids == [i for i in control.ids if i in ref_by_id] and set(control.ids) == set(ref_by_id) \
            and all(ref_by_id[i] == d for i, d in zip(control.ids, control.documents))
        log("2/4 kontrol", f"Normalizasyon kapalı process_pdf: {len(control.ids)} chunk, baseline ile birebir aynı: {control_match}")
        if not control_match:
            raise RuntimeError("process_pdf without normalization does not reproduce the baseline; comparison would not be fair")

        #Normalizasyon açık, embedding yok: yeni chunk'ları önceden görüp toplam sayıyı ve istatistikleri çıkarıyoruz
        planned = CaptureDB()
        run_process_pdf(ingestion, planned, pdf_paths, normalize=True)
        log("2/4 kontrol", f"Normalizasyon açık process_pdf: {len(planned.ids)} chunk üretecek (baseline {len(control.ids)})")

        from langchain_huggingface import HuggingFaceEmbeddings
        from langchain_chroma import Chroma
        log("3/4 embedding", f"{MODEL_NAME} yükleniyor (CPU)...")
        base_emb = HuggingFaceEmbeddings(model_name=MODEL_NAME, model_kwargs={"device": "cpu"})
        progress = ProgressEmbeddings(base_emb, len(planned.ids))
        #process_pdf'in kullandığıyla aynı tür DB: LangChain Chroma + düz HuggingFaceEmbeddings (ön ek yok)
        real_db = Chroma(collection_name=COLLECTION_NAME, persist_directory=str(partial), embedding_function=progress)
        embed_started = time.perf_counter()
        results = run_process_pdf(ingestion, real_db, pdf_paths, normalize=True)
        embed_seconds = time.perf_counter() - embed_started
        real_db = None
        release_chroma()
        log("3/4 embedding", f"process_pdf sonuçları (chunk sayıları): {[r.get('total_chunks') for r in results]}")

        log("4/4 doğrulama", "Yeni DB geçici bir kopyada açılıp planlanan chunk'larla karşılaştırılıyor...")
        verify_root = Path(tempfile.mkdtemp(prefix="chroma_norm_verify_"))
        try:
            shutil.copytree(partial, verify_root / "db")
            import chromadb
            client = chromadb.PersistentClient(path=str(verify_root / "db"))
            try:
                col = client.get_collection(COLLECTION_NAME)
                data = col.get(include=["documents", "metadatas", "embeddings"])
                got = {i: (d, m, e) for i, d, m, e in zip(data["ids"], data["documents"], data["metadatas"], data["embeddings"])}
                want = {i: (d, m) for i, d, m in zip(planned.ids, planned.documents, planned.metadatas)}
                sample = planned.ids[:: max(1, len(planned.ids) // CONSISTENCY_SAMPLE_SIZE)][:CONSISTENCY_SAMPLE_SIZE]
                fresh = base_emb.embed_documents([want[i][0] for i in sample])
                probe = base_emb.embed_query("query: kontrol")
                verification = {
                    "count": col.count(),
                    "ids_match": set(got) == set(want),
                    "documents_match": all(got[i][0] == want[i][0] for i in want),
                    "page_and_source_match": all((got[i][1]["source_file"], got[i][1]["page_number"]) == (want[i][1]["source_file"], want[i][1]["page_number"]) for i in want),
                    "hnsw_match_reference": col.configuration_json["hnsw"] == ref["configuration"]["hnsw"],
                    "configuration": col.configuration_json,
                    "reference_configuration": ref["configuration"],
                    "consistency_min_cosine_no_prefix": round(min(cosine([float(x) for x in got[i][2]], v) for i, v in zip(sample, fresh)), 6),
                    "searchable": len(col.query(query_embeddings=[probe], n_results=col.count())["ids"][0]),
                }
            finally:
                client = None
                release_chroma()
        finally:
            shutil.rmtree(verify_root, ignore_errors=True)
        log("4/4 doğrulama", str({k: v for k, v in verification.items() if "configuration" not in k}))
        problems = [k for k in ("ids_match", "documents_match", "page_and_source_match", "hnsw_match_reference") if not verification[k]]
        if verification["count"] != len(planned.ids) or verification["searchable"] != len(planned.ids):
            problems.append("count/searchable")
        if verification["consistency_min_cosine_no_prefix"] < 0.999:
            problems.append("consistency")
        if problems:
            raise RuntimeError(f"New DB verification failed: {problems}; left at {partial}")
        partial.rename(target)

        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "description": "PDFs re-ingested through services.ingestion.process_pdf with normalize_blank_lines; all other settings as baseline",
            "reference": os.path.relpath(reference),
            "target": os.path.relpath(target),
            "pdfs": pdf_names,
            "model": MODEL_NAME,
            "collection": COLLECTION_NAME,
            "control_reproduces_baseline": control_match,
            "baseline_chunks": chunk_stats(control, raw_pages),
            "normalized_chunks": chunk_stats(planned, norm_pages),
            "chunk_ids_kept": len(set(control.ids) & set(planned.ids)),
            "chunk_texts_kept": len(set(control.documents) & set(planned.documents)),
            "verification": verification,
            "embedding_seconds": round(embed_seconds, 1),
        }
    except BaseException as exc:
        error = exc
    finally:
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)
        ref_unchanged = fingerprint(reference) == before_ref
        also_unchanged = before_also is None or fingerprint(also) == before_also
        log("son", f"{reference.name} değişmedi: {ref_unchanged} | {also.name} değişmedi: {also_unchanged}")

    if error is not None:
        print(f"ERROR: {error!r}")
        sys.exit(1)
    if not (ref_unchanged and also_unchanged):
        sys.exit("ERROR: an existing Chroma directory changed during the run")

    import chromadb
    import sentence_transformers
    manifest.update({
        "reference_unchanged": ref_unchanged,
        "passage_db_unchanged": also_unchanged,
        "total_seconds": round(time.perf_counter() - started, 1),
        "chromadb_version": chromadb.__version__,
        "sentence_transformers_version": sentence_transformers.__version__,
    })
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    log("son", f"Tamamlandı: {target} ({fmt_seconds(manifest['total_seconds'])}). Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
