import os
import re
import math
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated
from fastapi import Depends, FastAPI, UploadFile, File, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, StringConstraints
from services.ingestion import PdfUnreadableError, process_pdf, hash_bytes, hash_pdf, is_file_already_processed, delete_document_chunks
from core.auth import check_auth_config, check_password, clear_session_cookie, login_guard, require_auth, set_session_cookie
from core.config import get_db, get_llm, get_reranker
from core.settings import get_settings
from schemas import DeleteResponse, DocumentItem, FeaturesResponse, QueryResponse, UploadResponse, UsageStatus
from services.query import LLMUnavailableError, prepare_chunks_with_metadata, warm_up
from services.usage import get_status, record_usage

#Tüm ayarlar core/settings.py'den (.env)
settings = get_settings()


#Başlangıçta API key ve DB kontrolü; eksikse uygulama ilk soruyu beklemeden hata verip durur
#Model ve DB burada yüklendiği için ilk soru da beklemiyor
@asynccontextmanager
async def lifespan(app):
    #Auth ayarları modeller yüklenmeden önce kontrol ediliyor; eksikse beklemeden hata verir (core/auth.py)
    check_auth_config()
    get_llm()
    get_db()
    if settings.reranker_enabled:
        get_reranker()
    #Arama ve reranker bir kez çalıştırılır; ilk soru ısınmayı beklemez. Hata verirse uygulama başlamaz
    warm_up()
    yield


app = FastAPI(title="Enterprise AI RAG", lifespan=lifespan)

#PDF'lerin kaydedileceği ana klasör yolu
UPLOAD_DIR = str(settings.upload_dir)
os.makedirs(UPLOAD_DIR, exist_ok=True)

#Upload sınırları
MAX_UPLOAD_BYTES = settings.max_upload_bytes
#Harf (Türkçe dahil), rakam, boşluk ve _ - . ( ) dışındaki karakterleri kabul etmiyoruz
SAFE_FILENAME = re.compile(r"[\w\- .()]+\.pdf", re.IGNORECASE)
#Aynı PDF iki sekmeden aynı anda yüklenirse ikisi de duplikasyon kontrolünü geçmesin diye yüklemeleri sıraya alıyoruz
#Silme de aynı kilidi kullanıyor: yarım kalmış bir upload'ın chunk'ları silinen dosyadan sonra DB'ye eklenmesin
#Sorgular bu kilidi beklemiyor
UPLOAD_LOCK = asyncio.Lock()

#Gemini'ye geçici olarak ulaşılamadığında dönen kod ve mesaj (services/query.py classify_llm_error)
#429 kullanılmıyor: frontend 429'u bizim token sınırımız sayıp girişi kilitliyor (services/api.ts TokenLimitError)
LLM_ERRORS = {
    "timeout": (504, "Yanıt zamanında alınamadı. Lütfen tekrar deneyin."),
    "quota": (503, "Yapay zekâ servisinin kullanım sınırı doldu. Lütfen daha sonra tekrar deneyin."),
    "server": (503, "Yapay zekâ servisi şu an yanıt vermiyor. Lütfen biraz sonra tekrar deneyin."),
    "connection": (503, "Yapay zekâ servisi şu an yanıt vermiyor. Lütfen biraz sonra tekrar deneyin."),
}

#Soru sınırı; frontenddeki MAX_QUESTION_LENGTH ile aynı olmalı (ChatArea.tsx)
MAX_QUESTION_LENGTH = settings.max_question_length

#Tüm modüller "EnterpriseAI" logger'ını kullanıyor; seviye .env'deki LOG_LEVEL'dan
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("EnterpriseAI")

#Frontend adresleri .env'den okunur; tanımlı değilse localhost:3000 varsayılır
CORS_ORIGINS = settings.cors_origins

#Oturum cookie'si (core/auth.py) frontend'den farklı porttaki backend'e gidiyor; tarayıcı cookie'yi ancak backend
#credentials'a izin verirse gönderir/kabul eder (frontend'de fetch credentials: "include")
#Credentials açıkken origin "*" olamaz; CORS_ORIGINS açık liste olduğu için güvenli
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)

#GLOBAL HATA YAKALAYICI
#Hiçbir yerde yakalanmayan hatalar düz metin "Internal Server Error" yerine JSON 500 olarak döner
#HTTPException (400/403/404/409/413/429/503/504) ve 422 doğrulama hataları kendi yakalayıcılarında kalıyor
#Bu yakalayıcı Starlette'in en dış katmanında (ServerErrorMiddleware) çalışıyor; CORS middleware'inin dışında
#kaldığı için başlığı elle ekliyoruz, yoksa tarayıcı yanıtı engeller ve frontend yalnızca "Failed to fetch" görür
#Starlette yanıttan sonra hatayı yeniden fırlatıyor; Uvicorn da traceback'i ayrıca logluyor (bilinçli olarak kabul edildi)
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Beklenmeyen hata ({request.method} {request.url.path})", exc_info=exc)
    headers = {}
    origin = request.headers.get("origin")
    if origin in CORS_ORIGINS:
        headers["Access-Control-Allow-Origin"] = origin
        #Credentials'lı isteklerde bu başlık yoksa tarayıcı yanıtı yine engeller
        headers["Access-Control-Allow-Credentials"] = "true"
        headers["Vary"] = "Origin"
    return JSONResponse(status_code=500, content={"detail": "Beklenmeyen bir hata oluştu."}, headers=headers)

#Pydantic modeli - Frontendden gelen jsonı karşıladı
#Boş, sadece boşluk veya sınırdan uzun sorular endpoint çalışmadan 422 alır; LLM çağrılmaz, token harcanmaz
class QueryRequest(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_LENGTH)]

#Giriş isteği; parola sınırı yalnızca aşırı uzun gövdeleri reddetmek için
class LoginRequest(BaseModel):
    password: Annotated[str, StringConstraints(min_length=1, max_length=256)]

#Frontend açılışta /auth/me'ye bakarak giriş ekranını gösterip göstermeyeceğine karar verir
class AuthStatus(BaseModel):
    authenticated: bool

#Hatalı giriş sayacı istemci (IP) bazında (core/auth.py LoginGuard)
def client_key(request: Request):
    return request.client.host if request.client else "unknown"

#Herkese açık: uygulamanın ayakta olduğunu gösterir, veri döndürmez
@app.get("/")
def read_root():
    return {"status": "Enterprise AI projesi ayakta."}

# KİMLİK DOĞRULAMA
#Parola doğruysa imzalı oturum cookie'si kurulur (204, gövde yok); parola ve token loglanmaz
#Kilitliyken parola hiç kontrol edilmez (429); 429 yalnızca bu endpoint'te giriş kilidi anlamında, frontend'deki
#token sınırı 429'u /query/'ye özgü (services/api.ts)
@app.post("/auth/login", status_code=204)
def login(body: LoginRequest, request: Request, response: Response):
    if not settings.auth_enabled:
        return
    client = client_key(request)
    remaining = login_guard.locked_seconds(client)
    if remaining:
        raise HTTPException(status_code=429, detail=f"Çok fazla hatalı deneme. {math.ceil(remaining / 60)} dakika sonra tekrar deneyin.")
    if not check_password(body.password):
        login_guard.record_failure(client)
        logger.warning("Hatalı giriş denemesi.")
        raise HTTPException(status_code=401, detail="Parola hatalı.")
    login_guard.record_success(client)
    set_session_cookie(response)
    logger.info("Giriş yapıldı.")

#Korumasız: cookie'yi silmek için oturum gerekmiyor (süresi dolmuş cookie de silinebilsin)
#Stateless oturum: yalnızca bu tarayıcıdaki cookie silinir (core/auth.py)
@app.post("/auth/logout", status_code=204)
def logout(response: Response):
    clear_session_cookie(response)

@app.get("/auth/me", response_model=AuthStatus, dependencies=[Depends(require_auth)])
def read_me():
    return AuthStatus(authenticated=True)

# KORUNAN ENDPOINT'LER
#Aşağıdakilerin hepsinde dependencies=[Depends(require_auth)]: cookie yoksa veya geçersizse endpoint çalışmadan 401
#Yeni endpoint eklenirken unutulmamalı

@app.get("/documents/", response_model=list[DocumentItem], dependencies=[Depends(require_auth)])
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
@app.get("/usage/", response_model=UsageStatus, dependencies=[Depends(require_auth)])
def read_usage():
    return get_status()

#Frontend silme butonunu göstermek için buraya bakar (.env ALLOW_DELETE)
@app.get("/features/", response_model=FeaturesResponse, dependencies=[Depends(require_auth)])
def read_features():
    return FeaturesResponse(allow_delete=settings.allow_delete)


#Dosya adındaki klasör kısmını atıyoruz (path traversal: "../x.pdf", "..\x.pdf"); güvenli değilse None
#Uzantıyı ".pdf"e çeviriyoruz ki /documents/ listesi ".PDF" ile yüklenenleri de göstersin
def clean_filename(filename):
    name = re.split(r"[\\/]", filename or "")[-1].strip()
    if name.startswith(".") or not SAFE_FILENAME.fullmatch(name):
        return None
    return name[:-4] + ".pdf"

#İşlem yarıda kaldıysa dosyayı diskte bırakmıyoruz; yoksa /documents/ listesinde hayalet doküman görünür
def remove_if_exists(path):
    if os.path.exists(path):
        os.remove(path)

#PDF önizleme (frontend iframe'i); eskiden auth'suz StaticFiles mount'uydu, URL aynı kaldı
#iframe header ekleyemez ama tarayıcı cookie'yi kendisi gönderdiği için auth burada da çalışıyor
#Ad kuralları silmedekiyle aynı: temizlenmiş ad istenenle aynı değilse (ör. "..\\x.pdf") reddediliyor
#{name} "/" içeremez; "../" ile klasör dışına çıkılamaz
@app.get("/static-pdfs/{name}", dependencies=[Depends(require_auth)])
def read_pdf(name: str):
    filename = clean_filename(name)
    if filename is None or filename != name:
        raise HTTPException(status_code=400, detail="Geçersiz dosya adı.")
    target_file_path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.isfile(target_file_path):
        raise HTTPException(status_code=404, detail=f"{filename} bulunamadı.")
    #inline: tarayıcı indirmek yerine iframe içinde gösterir
    return FileResponse(target_file_path, media_type="application/pdf", filename=filename, content_disposition_type="inline")

@app.post("/upload-pdf/", response_model=UploadResponse, dependencies=[Depends(require_auth)])
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

            #Model try içinde kuruluyor: biçim bozuksa da aynı temizlik ve genel 500 mesajı devreye girer
            response = UploadResponse(**result)
            logger.info(f"PDF DB'ye kaydedildi: {filename}")
            return response

        #Dosya PDF ama işlenemiyor (bozuk, şifreli, metinsiz); kullanıcı kaynaklı olduğu için 500 değil 422
        #Mesaj ingestion.py'de kullanıcıya gösterilecek biçimde yazılı; log orada WARNING
        except PdfUnreadableError as e:
            remove_if_exists(target_file_path)
            raise HTTPException(status_code=422, detail=str(e))

        except Exception:
            remove_if_exists(target_file_path)
            logger.exception("Upload hatası")
            raise HTTPException(status_code=500, detail="Dosya işlenirken beklenmeyen bir hata oluştu.")


@app.delete("/documents/{name}", response_model=DeleteResponse, dependencies=[Depends(require_auth)])
async def delete_document(name: str):
    #Kimlik doğrulama yokken herkes silebileceği için .env'de ALLOW_DELETE=true yapılmadıkça kapalı
    if not settings.allow_delete:
        raise HTTPException(status_code=403, detail="Doküman silme kapalı.")

    #Upload'daki ad kuralları; temizlenmiş ad istenen adla aynı değilse (ör. "..\\x.pdf") reddediyoruz
    filename = clean_filename(name)
    if filename is None or filename != name:
        raise HTTPException(status_code=400, detail="Geçersiz dosya adı.")

    async with UPLOAD_LOCK:
        target_file_path = os.path.join(UPLOAD_DIR, filename)
        file_exists = os.path.exists(target_file_path)
        try:
            #Önce chunk'lar, sonra dosya: DB silme başarısız olursa dosya yerinde kalır, sistem tutarlı kalır
            deleted_chunks = await run_in_threadpool(delete_document_chunks, filename)
            if file_exists:
                os.remove(target_file_path)
        except Exception:
            logger.exception("Silme hatası")
            raise HTTPException(status_code=500, detail="Doküman silinirken beklenmeyen bir hata oluştu.")

    if deleted_chunks == 0 and not file_exists:
        raise HTTPException(status_code=404, detail=f"{filename} bulunamadı.")

    logger.info(f"Doküman silindi: {filename} ({deleted_chunks} parça)")
    return DeleteResponse(name=filename, deleted_chunks=deleted_chunks)


#async değil: FastAPI def endpoint'leri thread'de çalıştırıyor, Gemini çağrısı sürerken diğer istekler donmuyor
@app.post("/query/", response_model=QueryResponse, dependencies=[Depends(require_auth)])
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
        logger.info(f"Bu sorunun token kullanımı: {current_token}")

        #Gerçek tüketimi kaydediyoruz; kırpma yok, son istek sınırı biraz aşabilir
        record_usage(current_token)
        usage = get_status()

        #Frontend sayaç ve kilit için kullanımı da yanıta ekliyoruz
        #Model try içinde kuruluyor: biçim bozuksa genel 500 mesajı döner; modelde olmayan alanlar yanıta girmez
        result = QueryResponse(**response, usage=usage)

        logger.info(f"Yanıt üretildi. Güncel token kullanımı: {usage['used']}/{usage['limit']}")
        return result

    #Geçici Gemini hataları (log services/query.py'de WARNING); başarısız istekte token kaydedilmiyor
    except LLMUnavailableError as e:
        status_code, detail = LLM_ERRORS[e.kind]
        raise HTTPException(status_code=status_code, detail=detail)

    except Exception:
        logger.exception("Query hatası")
        raise HTTPException(status_code=500, detail="Sorunuz yanıtlanırken beklenmeyen bir hata oluştu.")