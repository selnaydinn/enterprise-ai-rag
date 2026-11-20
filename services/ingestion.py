import os
import re
import hashlib
import logging
from langchain_community.document_loaders import PyPDFLoader
from pypdf.errors import PyPdfError
from core.config import get_db
from core.settings import get_settings
#CHUNKING_STRATEGIES burada da erişilebilir kalsın (scripts/build_db.py ingestion üzerinden okuyor)
from services.chunking import CHUNKING_STRATEGIES, split_pages  # noqa: F401

logger = logging.getLogger("EnterpriseAI")

#PDF kullanıcı kaynaklı bir nedenle işlenemediğinde fırlatılır (bozuk, şifreli, metinsiz)
#Mesaj kullanıcıya gösterilir; main.py dosyayı diskten silip 422 döner
class PdfUnreadableError(Exception):
    pass

#PDFleri hashledik aynı pdf yüklenmesin diye
#Format (md5'in ilk 8 karakteri) DB'deki pdf_hash değerleriyle aynı kalmalı
def hash_bytes(data):
    return hashlib.md5(data).hexdigest()[:8]

#Diskteki PDF'in hashi; upload'da dosya yazılmadan önce hash_bytes doğrudan kullanılabilir
def hash_pdf(pdf_path):
    with open(pdf_path, "rb") as f:
        return hash_bytes(f.read())
    
#pypdf boş satırları "\n \n" gibi boşluklu üretiyor; splitter'ın "\n\n" ayracı bunları tanıyabilsin diye
#yalnızca boşluktan oluşan satırları boş satıra çeviriyoruz (diğer satır sonlarına dokunmuyoruz)
def normalize_blank_lines(text):
    return re.sub(r"\n[^\S\n]+(?=\n)", "\n", text)

#Bir PDF'in DB'deki durumu (aynı PDF kontrolü ve yarım kalan yüklemeler; notes2.md Faz 2, sahipsiz parçalar)
#  missing:    hiç parçası yok
#  complete:   parça sayısı chunk_total'a eşit
#  incomplete: parça sayısı chunk_total'dan az; yükleme yarıda kalmış (uygulama yükleme sırasında öldürüldü)
#  legacy:     parçalarda chunk_total yok (bu alandan önce yüklenmiş); tam mı bilinemez, tam sayılır
#found: DB'deki parça sayısı, total: beklenen sayı (legacy ve missing'de None)
def document_status(file_hash, db=None):
    db = db if db is not None else get_db()
    metadatas = db.get(where={"pdf_hash": file_hash}, include=["metadatas"])["metadatas"]
    found = len(metadatas)
    if found == 0:
        return {"state": "missing", "found": 0, "total": None}
    totals = {meta.get("chunk_total") for meta in metadatas}
    if None in totals:
        return {"state": "legacy", "found": found, "total": None}
    total = max(totals)
    return {"state": "incomplete" if found < total else "complete", "found": found, "total": total}

#DOKÜMAN KONTROLÜ (açılışta, main.py lifespan; notes2.md Faz 2, sahipsiz parçalar)
#inspect_documents yalnızca bakar; repair_incomplete yalnızca yarım kalmış yüklemeleri geri alır
#Yarım kalma kesin (parça sayısı < chunk_total): silinen şey, uygulama çökmeseydi upload'daki geri almanın sileceğinin aynısı
#Diğer tutarsızlıklar (dosyası olmayan parçalar, parçası olmayan dosyalar, eski kayıtlar) yalnızca raporlanır
#UYARI: Tek uygulama kopyası varsayılıyor. Açılışta devam eden yükleme olamayacağı için "eksik" = "çökmüş"tür.
#Birden fazla kopya çalışırsa bir kopyanın süren yüklemesi diğerine yarım görünür ve silinir; o zaman yükleme durum
#kaydı (kalp atışı) veya tek bir ingestion işçisi gerekir (notes2.md Faz 11)

#DB'deki her PDF (pdf_hash'e göre) ve uploaded_pdfs/'teki dosyalar karşılaştırılır
#documents: her PDF için state (complete / incomplete / legacy), found, total, names (DB'deki adlar) ve
#  on_disk (diskte aynı adla VE aynı içerikle duran dosyalar; boşsa parçaların dosyası yok)
#files_without_chunks: diskte olup DB'de hiç parçası olmayan PDF'ler
def inspect_documents(upload_dir, db=None):
    db = db if db is not None else get_db()
    groups = {}
    for meta in db.get(include=["metadatas"])["metadatas"]:
        meta = meta or {}
        group = groups.setdefault(meta.get("pdf_hash"), {"names": set(), "found": 0, "totals": set()})
        group["names"].add(meta.get("source_file"))
        group["found"] += 1
        group["totals"].add(meta.get("chunk_total"))

    disk = {}
    if os.path.isdir(upload_dir):
        for name in sorted(os.listdir(upload_dir)):
            path = os.path.join(upload_dir, name)
            if name.endswith(".pdf") and os.path.isfile(path):
                disk[name] = hash_pdf(path)

    documents = []
    for pdf_hash, group in groups.items():
        names = sorted(n for n in group["names"] if n)
        #pdf_hash'i olmayan parçalar da eski kayıt sayılır: hangi yüklemeye ait olduğu bilinemez, dokunulmaz
        if None in group["totals"] or pdf_hash is None:
            state, total = "legacy", None
        else:
            total = max(group["totals"])
            state = "incomplete" if group["found"] < total else "complete"
        documents.append({
            "pdf_hash": pdf_hash, "names": names, "found": group["found"], "total": total, "state": state,
            "on_disk": [n for n in names if disk.get(n) == pdf_hash],
        })
    documents.sort(key=lambda d: (d["names"] or [""])[0])
    files_without_chunks = [name for name, file_hash in disk.items() if file_hash not in groups]
    return {"documents": documents, "files_without_chunks": files_without_chunks}


#Yarım kalmış yüklemeleri geri alır: parçalar hash ile silinir, dosya yalnızca adı ve içeriği o yüklemeyle birebir
#aynıysa silinir (silmeden hemen önce hash yeniden kontrol edilir). Geri alınan PDF'lerin listesini döner
def repair_incomplete(report, upload_dir, db=None):
    repaired = []
    for doc in report["documents"]:
        if doc["state"] != "incomplete":
            continue
        deleted = delete_chunks_by_hash(doc["pdf_hash"], db=db)
        removed = []
        for name in doc["on_disk"]:
            path = os.path.join(upload_dir, name)
            if os.path.isfile(path) and hash_pdf(path) == doc["pdf_hash"]:
                os.remove(path)
                removed.append(name)
        repaired.append({**doc, "deleted": deleted, "removed_files": removed})
    return repaired


#Açılış kontrolü: yarım kalmışları geri alır, gerisini loglar. Raporu döner
def check_documents(upload_dir, db=None):
    db = db if db is not None else get_db()
    report = inspect_documents(upload_dir, db=db)
    problems = 0

    for doc in repair_incomplete(report, upload_dir, db=db):
        problems += 1
        name = ", ".join(doc["names"]) or doc["pdf_hash"]
        file_part = "dosya kaldırıldı" if doc["removed_files"] else "dosyası yoktu"
        logger.warning(f"Yarım kalmış yükleme geri alındı: {name} ({doc['found']}/{doc['total']} parça silindi, "
                       f"{file_part}); PDF yeniden yüklenmeli.")

    for doc in report["documents"]:
        if doc["state"] != "incomplete" and not doc["on_disk"]:
            problems += 1
            name = ", ".join(doc["names"]) or f"(adsız, pdf_hash {doc['pdf_hash']})"
            logger.warning(f"Dosyası olmayan doküman: {name} ({doc['found']} parça DB'de, uploaded_pdfs/ içinde "
                           f"aynı içerikli dosya yok); kaynağa tıklanınca PDF açılmaz.")

    for name in report["files_without_chunks"]:
        problems += 1
        logger.warning(f"DB'de parçası olmayan PDF: {name} (listede görünür ama aramada çıkmaz).")

    counts = {state: sum(d["state"] == state for d in report["documents"]) for state in ("complete", "legacy")}
    result = "sorun yok" if problems == 0 else f"{problems} sorun (yukarıda)"
    logger.info(f"Doküman kontrolü: {len(report['documents'])} doküman ({counts['complete']} tam, "
                f"{counts['legacy']} eski kayıt); {result}.")
    return report


#Bir dokümanın tüm chunk'larını DB'den siler, silinen chunk sayısını döner
#source_file ile arıyoruz: aynı adla farklı içerik yüklenemediği için (409) ad tek bir PDF'i gösteriyor
def delete_document_chunks(pdf_name):
    db = get_db()
    ids = db.get(where={"source_file": pdf_name}, include=["metadatas"])["ids"]
    if ids:
        db.delete(ids=ids)
    return len(ids)

#Yarıda kalan bir yüklemenin chunk'larını geri alır (main.py upload), silinen chunk sayısını döner
#Hash ile arıyoruz: dosya adı değil içerik; yazılmış olan her chunk'ın metadata'sında pdf_hash var (prepare_chunks)
#Yalnızca bu yüklemenin chunk'ları silinir: aynı hash DB'de önceden olsaydı upload duplikasyon kontrolünde 409 alırdı
#db verilmezse uygulamanın DB'si (testler sahte DB veriyor)
def delete_chunks_by_hash(pdf_hash, db=None):
    db = db if db is not None else get_db()
    ids = db.get(where={"pdf_hash": pdf_hash}, include=[])["ids"]
    if ids:
        db.delete(ids=ids)
    return len(ids)

#PDF HASH, CHUNKING
#Bölme yöntemleri services/chunking.py'de (split_pages, CHUNKING_STRATEGIES)
#PDF'i okuyup chunk'larını ve id'lerini hazırlar; DB'ye dokunmaz (scripts/build_db.py embedding'den önce chunk'ları buradan görüyor)
#strategy verilmezse core/settings.py chunking_strategy
def prepare_chunks(pdf_path, strategy=None):

    # Dosya adını loglar için yoldan ayıklıyoruz
    pdf_name = os.path.basename(pdf_path)

    #Hata sessiz bir sözlük olarak değil exception olarak dönüyor; main.py dosyayı temizleyip
    #PdfUnreadableError'da 422, diğer hatalarda 500 veriyor
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"Dosya bulunamadı: {pdf_path}")

    #Hashing
    #Duplikasyon kontrolü burada değil, main.py'de diske yazmadan önce (UPLOAD_LOCK içinde) yapılıyor
    #Doğrudan çağrılsa da chunk id'leri hash'ten türediği için aynı PDF DB'ye ikinci kez eklenmez
    pdf_hash = hash_pdf(pdf_path)

    logger.info(f"{pdf_name} analiz ediliyor...")

    #PDFi sayfa sayfa döküman objesi yaptık
    #"%PDF-" ile başlayıp okunamayan dosyalar (bozuk, şifreli, boş) burada yakalanıyor
    #pypdf'in tüm okuma hataları PyPdfError'dan türüyor (PdfReadError, FileNotDecryptedError, EmptyFileError...)
    loader = PyPDFLoader(pdf_path)
    try:
        pages = loader.load()
    except PyPdfError as exc:
        logger.warning(f"{pdf_name} okunamadı ({type(exc).__name__}).")
        raise PdfUnreadableError("Dosya okunamadı; bozuk veya şifreli olabilir.") from exc
    for page in pages:
        page.page_content = normalize_blank_lines(page.page_content)

    #Sayfaları chunklara böldük
    chunks = split_pages(pages, strategy or get_settings().chunking_strategy)

    logger.info(f"{pdf_name}: {len(pages)} sayfa okundu, {len(chunks)} parçaya bölündü.")

    #Taranmış (görüntü) PDF'lerde metin çıkmıyor; 0 parçayla "başarılı" dönmek yerine reddediyoruz
    #DB'ye yazmadan önce kontrol ediliyor; yarım kayıt kalmıyor
    if not chunks:
        logger.warning(f"{pdf_name}: okunabilir metin bulunamadı.")
        raise PdfUnreadableError("PDF'te okunabilir metin bulunamadı (taranmış olabilir).")

    final_chunks = []
    chunk_ids = []

    #Metadata ataması
    #chunk_total: PDF'in toplam parça sayısı; DB'deki parça sayısı bundan azsa yükleme yarıda kalmıştır (notes2.md Faz 2,
    #sahipsiz parçalar). Bu alandan önce yüklenmiş parçalarda yok. Metadata vektöre girmez, aramayı etkilemez
    for idx, chunk in enumerate(chunks):
        if "page" in chunk.metadata:
            page_num_of_chunk = int(chunk.metadata["page"]) + 1
        elif "page_label" in chunk.metadata:
            page_num_of_chunk = int(chunk.metadata["page_label"])
        else:
            page_num_of_chunk = 1

        #Her chunkın idsi var
        unique_id_of_chunk = f"h_{pdf_hash}_p{page_num_of_chunk}_c{idx}"

        chunk.metadata["source_file"] = pdf_name
        chunk.metadata["page_number"] = page_num_of_chunk
        chunk.metadata["pdf_hash"] = pdf_hash
        chunk.metadata["chunk_total"] = len(chunks)

        final_chunks.append(chunk)
        chunk_ids.append(unique_id_of_chunk)

    return {"pdf_name": pdf_name, "pdf_hash": pdf_hash, "total_pages": len(pages), "chunks": final_chunks, "ids": chunk_ids}


#PDF HASH, CHUNKING AND  VEKTORIZATION
#db verilmezse uygulamanın DB'si (get_db); scripts/build_db.py yeni kurduğu DB'yi veriyor
def process_pdf(pdf_path, db=None, strategy=None):
    prepared = prepare_chunks(pdf_path, strategy)
    pdf_name, final_chunks, chunk_ids = prepared["pdf_name"], prepared["chunks"], prepared["ids"]

    logger.info(f"{len(final_chunks)} parça vektörize edilecek.")

    #VECTORIZE CHUNKS
    #Tüm dökümanı tek seferde değil, 32şerli paketlerle gömerek CPU'yu rahatlatıyoruz
    #Bir paket hata verirse önceki paketler DB'de kalır; main.py upload'u delete_chunks_by_hash ile geri alıyor
    #langchain_chroma upsert ile yazıyor ve id'ler hash'ten türüyor: aynı PDF yeniden işlenirse kayıt çoğalmaz
    batch_size = 32
    db = db if db is not None else get_db()
    for i in range(0, len(final_chunks), batch_size):
        batch_chunks = final_chunks[i : i + batch_size]
        batch_ids = chunk_ids[i : i + batch_size]

        logger.debug(f"{i}/{len(final_chunks)} arası parçalar vektörize ediliyor...")
        db.add_documents(documents=batch_chunks, ids=batch_ids)

    logger.info(f"{pdf_name} vektörize edildi.")

    #Alan adları schemas.py UploadResponse ile aynı
    return {
        "filename": pdf_name,
        "total_pages": prepared["total_pages"],
        "total_chunks": len(final_chunks),
        "message": f"{pdf_name} başarıyla vektörize edildi."
    }