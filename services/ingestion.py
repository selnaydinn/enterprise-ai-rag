import os
import hashlib
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from core.config import db

#PDFleri hashledik aynı pdf yüklenmesin diye
def hash_pdf(pdf_path):
    with open(pdf_path, "rb") as f:
        file_bytes = f.read()
        return hashlib.md5(file_bytes).hexdigest()[:8]
    
#Aynı pdf kontrolü
def is_file_already_processed(file_hash):
    existing_docs = db.get(where={"pdf_hash": file_hash}, limit=1)
    return len(existing_docs["ids"]) > 0

#PDF HASH, CHUNKING AND  VEKTORIZATION
def process_pdf(pdf_path):

    # Dosya adını loglar için yoldan ayıklıyoruz
    pdf_name = os.path.basename(pdf_path)

    if not os.path.exists(pdf_path):
        print(f"❌ [HATA]: Dosya bulunamadı! Yol: {pdf_path}")
        return {"status": "Hata", "message": "Dosya sistemde bulunamadı."}

    #Hashing
    pdf_hash = hash_pdf(pdf_path)

    #Check
    if is_file_already_processed(pdf_hash):
        print(f" 🛡️ [SİSTEM KORUMASI]: {pdf_name} (Hash: {pdf_hash}) zaten veritabanında kayıtlı!")
        return {
            "status": "Reddedildi",
            "message": f"{pdf_name} zaten veritabanında mevcut."
        }

    print(f"🚀 {pdf_name} yeni bir döküman olarak algılandı, analiz ediliyor...")

    #PDFi sayfa sayfa döküman objesi yaptık
    loader = PyPDFLoader(pdf_path)
    pages = loader.load()

    #Sayfaları chunklara böldük
    splitter = RecursiveCharacterTextSplitter(
        chunk_size = 1200,
        chunk_overlap = 300
    )
    chunks = splitter.split_documents(pages)

    print(f"📄 {pdf_name} adlı pdf {len(pages)} sayfa olarak okundu.")
    print(f"✂️ {pdf_name} adlı pdf {len(chunks)} kadar anlamsal parçaya bölündü.")

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

    print(f"📦 {len(final_chunks)} kadar parça database için hazır.")

    #VECTORIZE CHUNKS
    #Tüm dökümanı tek seferde değil, 32şerli paketlerle gömerek CPU'yu rahatlatıyoruz
    batch_size = 32
    for i in range(0, len(final_chunks), batch_size):
        batch_chunks = final_chunks[i : i + batch_size]
        batch_ids = chunk_ids[i : i + batch_size]
        
        print(f"{i}/{len(final_chunks)} arası parçalar vektörize ediliyor...")
        db.add_documents(documents=batch_chunks, ids=batch_ids)

    print(f"{pdf_name} adlı yönetmelik başarıyla vektörize edildi.")

    return {
        "status": "Success",
        "total_pages": len(pages),
        "total_chunks": len(chunks),
        "message": f"{pdf_name} başarıyla vektörize edildi."
    }