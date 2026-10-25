import os
import re
import asyncio
import logging
from datetime import datetime
from typing import Annotated
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, StringConstraints
from services.ingestion import process_pdf, hash_bytes, hash_pdf, is_file_already_processed
from core.config import db
from services.query import prepare_chunks_with_metadata
from services.usage import get_status, record_usage
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Enterprise AI RAG")

#PDF'lerin kaydedileceği ana klasör yolu
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploaded_pdfs")
os.makedirs(UPLOAD_DIR, exist_ok=True)

#Upload sınırları
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
#Harf (Türkçe dahil), rakam, boşluk ve _ - . ( ) dışındaki karakterleri kabul etmiyoruz
SAFE_FILENAME = re.compile(r"[\w\- .()]+\.pdf", re.IGNORECASE)
#Aynı PDF iki sekmeden aynı anda yüklenirse ikisi de duplikasyon kontrolünü geçmesin diye yüklemeleri sıraya alıyoruz
#Sorgular bu kilidi beklemiyor
UPLOAD_LOCK = asyncio.Lock()

#Soru sınırı; frontenddeki MAX_QUESTION_LENGTH ile aynı olmalı (ChatArea.tsx)
MAX_QUESTION_LENGTH = 500

#Frontendin PDF'leri okuyabilmesi için statik yönlendirme
app.mount("/static-pdfs", StaticFiles(directory=UPLOAD_DIR), name="pdfs")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("EnterpriseAI")

#Frontend adresleri .env'den okunur; tanımlı değilse localhost:3000 varsayılır
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if o.strip()]

#Frontend cookie kullanmıyor, credentials kapalı
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

#Pydantic modeli - Frontendden gelen jsonı karşıladı
#Boş, sadece boşluk veya sınırdan uzun sorular endpoint çalışmadan 422 alır; LLM çağrılmaz, token harcanmaz
class QueryRequest(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_LENGTH)]

@app.get("/")
def read_root():
    return {"status": "Enterprise AI projesi ayakta."}

@app.get("/documents/")
def list_documents():
    try:
        # Klasördeki tüm dosyaları listele ve sadece .pdf olanları seç
        if os.path.exists(UPLOAD_DIR):
            pdf_files = [f for f in os.listdir(UPLOAD_DIR) if f.endswith('.pdf')]
            # Frontendin beklediği [{name: "..."}] formatına çeviriyoruz
            return [{"name": f} for f in pdf_files]
        return []
    except Exception:
        #Hata detayı (dosya yolları vb.) sadece loga yazılıyor; kullanıcıya genel mesaj dönüyor
        logger.exception("Dosya listeleme hatası")
        raise HTTPException(status_code=500, detail="Doküman listesi alınırken bir hata oluştu.")

#Frontend sayfa açılışında token sayacını ve kilit durumunu buradan okur
@app.get("/usage/")
def read_usage():
    return get_status()


#Dosya adındaki klasör kısmını atıyoruz (path traversal: "../x.pdf", "..\x.pdf"); güvenli değilse None
#Uzantıyı ".pdf"e çeviriyoruz ki /documents/ listesi ".PDF" ile yüklenenleri de göstersin
def clean_filename(filename):
    name = re.split(r"[\\/]", filename or "")[-1].strip()
    if name.startswith(".") or not SAFE_FILENAME.fullmatch(name):
        return None
    return name[:-4] + ".pdf"

@app.post("/upload-pdf/")
async def upload_pdf(file: UploadFile = File(...)):
    filename = clean_filename(file.filename)
    if filename is None:
        raise HTTPException(status_code=400, detail="Geçersiz dosya adı. Ad .pdf ile bitmeli; yalnızca harf, rakam, boşluk ve _ - . ( ) içerebilir.")

    #Sınırın 1 bayt fazlasını okuyoruz; o kadar gelirse dosya sınırı aşıyor demektir
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"Dosya boyutu {MAX_UPLOAD_BYTES // (1024 * 1024)} MB sınırını aşıyor.")

    #Uzantıya değil içeriğe bakıyoruz: PDF dosyaları "%PDF-" ile başlar
    if not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="Dosya geçerli bir PDF değil.")

    #Duplikasyon kontrolünü diske yazmadan önce yapıyoruz; reddedilen dosya diskte kalmasın
    pdf_hash = hash_bytes(content)

    #Kontrolden DB'ye kayda kadar tek yükleme çalışıyor; yoksa iki eş yükleme kontrolü birlikte geçip chunk'ları iki kez ekler
    async with UPLOAD_LOCK:
        if is_file_already_processed(pdf_hash):
            raise HTTPException(status_code=409, detail=f"{filename} zaten veritabanında mevcut.")

        #Kaydedilecek dosyanın tam yolunu oluşturuyoruz
        target_file_path = os.path.join(UPLOAD_DIR, filename)

        #Aynı adla farklı içerik gelirse eski PDF'in üzerine yazmıyoruz (eski chunk'lar DB'de bu adla duruyor)
        if os.path.exists(target_file_path) and hash_pdf(target_file_path) != pdf_hash:
            raise HTTPException(status_code=409, detail=f"{filename} adında farklı içerikli bir dosya zaten var. Dosyayı yeniden adlandırıp yükleyin.")

        try:
            #Gelen dosyayı kalıcı klasöre yazıyoruz
            with open(target_file_path, "wb") as f:
                f.write(content)

            logger.info(f"Dosya kalıcı olarak kaydedildi: {target_file_path}")

            #PDFi vektörize eden fonksiyonu çağırdık
            #Embedding CPU yoğun; thread'de çalışınca diğer istekler (sorgu, liste) beklemiyor
            result = await run_in_threadpool(process_pdf, target_file_path)

            logger.info("Pdf dosyası DB'ye başarıyla kaydedildi!!!")
            return result

        except Exception:
            #İşlem yarıda kaldıysa dosyayı diskte bırakmıyoruz; yoksa /documents/ listesinde hayalet doküman görünür
            if os.path.exists(target_file_path):
                os.remove(target_file_path)
            logger.exception("Upload hatası")
            raise HTTPException(status_code=500, detail="Dosya işlenirken beklenmeyen bir hata oluştu.")


#async değil: FastAPI def endpoint'leri thread'de çalıştırıyor, Gemini çağrısı sürerken diğer istekler donmuyor
@app.post("/query/")
def query(request: QueryRequest):
    #Token sınırı aşım kontrolü; istek hatalı değil, kota dolu olduğu için 429
    #Sınır saate bağlı pencerelerle (Türkiye saati) sıfırlanıyor, kullanıcıya sıfırlanma saatini gösteriyoruz
    usage = get_status()
    if usage["limited"]:
       reset_time = datetime.fromisoformat(usage["resets_at"]).strftime("%H:%M")
       raise HTTPException(
           status_code=429,
           detail=f"Token kullanım sınırına ulaşıldı. Sınırın sıfırlanma saati: {reset_time}."
       )

    try:
        #İlgili dökümanları bulup LLM'den cevap dönen fonksiyonu çağırdık
        response = prepare_chunks_with_metadata(request.question)

        #O anki isteğin token miktarı
        current_token = response.get("token_usage", 0)
        logger.info(f"Token usage alındı: {current_token}")

        #Gerçek tüketimi kaydediyoruz; kırpma yok, son istek sınırı biraz aşabilir
        record_usage(current_token)
        usage = get_status()

        #Frontend sayaç ve kilit için kullanımı okuyabilsin diye objeye ekliyoruz
        response["usage"] = usage

        logger.info(f"AI başarıyla yanıtını döndü!!! Güncel Kümülatif Token: {usage['used']}")
        return response

    except Exception:
        logger.exception("Query hatası")
        raise HTTPException(status_code=500, detail="Sorunuz yanıtlanırken beklenmeyen bir hata oluştu.")