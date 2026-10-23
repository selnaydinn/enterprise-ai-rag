import os
import re
import logging
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from services.ingestion import process_pdf, hash_bytes, hash_pdf, is_file_already_processed
from core.config import db
from services.query import prepare_chunks_with_metadata
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Enterprise AI RAG")

#TOKEN 
TOTAL_TOKEN_USAGE = 0
TOKEN_LIMIT = 10000

#PDF'lerin kaydedileceği ana klasör yolu
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploaded_pdfs")
os.makedirs(UPLOAD_DIR, exist_ok=True)

#Upload sınırları
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
#Harf (Türkçe dahil), rakam, boşluk ve _ - . ( ) dışındaki karakterleri kabul etmiyoruz
SAFE_FILENAME = re.compile(r"[\w\- .()]+\.pdf", re.IGNORECASE)

#Frontendin PDF'leri okuyabilmesi için statik yönlendirme
app.mount("/static-pdfs", StaticFiles(directory=UPLOAD_DIR), name="pdfs")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("EnterpriseAI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

#Pydantic modeli - Frontendden gelen jsonı karşıladı
class QueryRequest(BaseModel):
    question: str

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
    except Exception as e:
        logger.error(f"Dosya listeleme hatası: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    

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
        result = process_pdf(target_file_path)

        logger.info("Pdf dosyası DB'ye başarıyla kaydedildi!!!")
        return result

    except Exception as e:
        #İşlem yarıda kaldıysa dosyayı diskte bırakmıyoruz; yoksa /documents/ listesinde hayalet doküman görünür
        if os.path.exists(target_file_path):
            os.remove(target_file_path)
        logger.error(f"Upload hatası: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/query/")
async def query(request: QueryRequest):
    global TOTAL_TOKEN_USAGE

    #Token sınırı aşım kontrolü
    if TOTAL_TOKEN_USAGE >= TOKEN_LIMIT:
       raise HTTPException(
           status_code=400,
           detail="Token sınırı aşıldı. 24 saat sonra sıfırlanacaktır."
       )
    
    try:
        #İlgili dökümanları bulup LLM'den cevap dönen fonksiyonu çağırdık
        response = prepare_chunks_with_metadata(request.question)

        #O anki isteğin token miktarı
        current_token = response.get("token_usage", 0)
        logger.info(f"Token usage alındı: {current_token}")   
        
        #Kümülatif toplamı güncelliyoruz
        TOTAL_TOKEN_USAGE += current_token

        if TOTAL_TOKEN_USAGE > TOKEN_LIMIT:
            TOTAL_TOKEN_USAGE = TOKEN_LIMIT

        #Kümülatif toplamı frontend okuyabilsin diye objeye ekliyoruz
        response["total_token_usage"] = {
            "total_tokens": TOTAL_TOKEN_USAGE
        }

        logger.info(f"AI başarıyla yanıtını döndü!!! Güncel Kümülatif Token: {TOTAL_TOKEN_USAGE}")
        return response

    except Exception as e:
        logger.error(f"Query hatası: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))