import logging
from functools import lru_cache
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain_google_genai import ChatGoogleGenerativeAI
from core.settings import get_settings

logger = logging.getLogger("EnterpriseAI")

#Model, DB ve LLM import anında değil ilk kullanımda oluşturulur, sonra aynı nesne tekrar kullanılır
#main.py başlangıçta (lifespan) hepsini çağırıyor; key veya DB eksikse uygulama ilk soruyu beklemeden hata verir


#LLM modeli (Gemini)
@lru_cache
def get_llm():
    settings = get_settings()
    #Key yoksa ilk sorguda değil, başlangıçta hata veriyoruz
    if settings.gemini_api_key is None:
        raise RuntimeError("GEMINI_API_KEY tanımlı değil. .env.example dosyasına bakın.")
    logger.info("Key etkinleştirildi.")

    logger.info(f"LLM modeli yükleniyor ({settings.llm_model})...")
    llm = ChatGoogleGenerativeAI(
        model = settings.llm_model,
        google_api_key = settings.gemini_api_key.get_secret_value(),
        temperature = settings.llm_temperature
    )
    logger.info("LLM modeli yüklendi.")
    return llm


#Embedding modeli
@lru_cache
def get_embeddings():
    settings = get_settings()
    logger.info(f"Embedding modeli yükleniyor ({settings.embedding_model})...")
    embeddings = HuggingFaceEmbeddings(
        model_name=settings.embedding_model,
        model_kwargs={'device': 'cpu'}
    )
    logger.info("Embedding modeli yüklendi.")
    return embeddings


#DB bağlantısı
@lru_cache
def get_db():
    settings = get_settings()
    #Uygulama DB oluşturmaz; DB yoksa boş bir DB açmak yerine hata veriyoruz
    if not (settings.chroma_dir / "chroma.sqlite3").is_file():
        raise RuntimeError(f"Chroma DB bulunamadı: {settings.chroma_dir}. CHROMA_DIR'i kontrol edin.")

    logger.info("Veritabanına bağlanılıyor...")
    db = Chroma(
        collection_name=settings.collection_name,
        persist_directory=str(settings.chroma_dir),
        embedding_function=get_embeddings()
    )
    logger.info("Veritabanına bağlanıldı.")
    return db
