import os
import re
import hashlib
import logging
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from core.config import get_db
from core.settings import get_settings

logger = logging.getLogger("EnterpriseAI")

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

#Aynı pdf kontrolü
def is_file_already_processed(file_hash):
    existing_docs = get_db().get(where={"pdf_hash": file_hash}, limit=1)
    return len(existing_docs["ids"]) > 0

#Bir dokümanın tüm chunk'larını DB'den siler, silinen chunk sayısını döner
#source_file ile arıyoruz: aynı adla farklı içerik yüklenemediği için (409) ad tek bir PDF'i gösteriyor
def delete_document_chunks(pdf_name):
    db = get_db()
    ids = db.get(where={"source_file": pdf_name}, include=["metadatas"])["ids"]
    if ids:
        db.delete(ids=ids)
    return len(ids)

#PDF HASH, CHUNKING AND  VEKTORIZATION
def process_pdf(pdf_path):

    # Dosya adını loglar için yoldan ayıklıyoruz
    pdf_name = os.path.basename(pdf_path)

    #Hata sessiz bir sözlük olarak değil exception olarak dönüyor; main.py dosyayı temizleyip 500 veriyor
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"Dosya bulunamadı: {pdf_path}")

    #Hashing
    #Duplikasyon kontrolü burada değil, main.py'de diske yazmadan önce (UPLOAD_LOCK içinde) yapılıyor
    #Doğrudan çağrılsa da chunk id'leri hash'ten türediği için aynı PDF DB'ye ikinci kez eklenmez
    pdf_hash = hash_pdf(pdf_path)

    logger.info(f"{pdf_name} analiz ediliyor...")

    #PDFi sayfa sayfa döküman objesi yaptık
    loader = PyPDFLoader(pdf_path)
    pages = loader.load()
    for page in pages:
        page.page_content = normalize_blank_lines(page.page_content)

    #Sayfaları chunklara böldük (boyutlar core/settings.py'de; değişirse DB yeniden kurulmalı)
    settings = get_settings()
    splitter = RecursiveCharacterTextSplitter(
        chunk_size = settings.chunk_size,
        chunk_overlap = settings.chunk_overlap
    )
    chunks = splitter.split_documents(pages)

    logger.info(f"{pdf_name}: {len(pages)} sayfa okundu, {len(chunks)} parçaya bölündü.")

    final_chunks = []
    chunk_ids = []

    #Metadata ataması
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

        final_chunks.append(chunk)
        chunk_ids.append(unique_id_of_chunk)

    logger.info(f"{len(final_chunks)} parça vektörize edilecek.")

    #VECTORIZE CHUNKS
    #Tüm dökümanı tek seferde değil, 32şerli paketlerle gömerek CPU'yu rahatlatıyoruz
    batch_size = 32
    db = get_db()
    for i in range(0, len(final_chunks), batch_size):
        batch_chunks = final_chunks[i : i + batch_size]
        batch_ids = chunk_ids[i : i + batch_size]
        
        logger.debug(f"{i}/{len(final_chunks)} arası parçalar vektörize ediliyor...")
        db.add_documents(documents=batch_chunks, ids=batch_ids)

    logger.info(f"{pdf_name} vektörize edildi.")

    #Alan adları schemas.py UploadResponse ile aynı
    return {
        "filename": pdf_name,
        "total_pages": len(pages),
        "total_chunks": len(chunks),
        "message": f"{pdf_name} başarıyla vektörize edildi."
    }