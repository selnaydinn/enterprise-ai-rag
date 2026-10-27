from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


#Tüm ayarlar tek yerde; değerler .env'den (veya ortam değişkenlerinden) okunur, yoksa varsayılan kullanılır
#Değişken adları büyük/küçük harf duyarsız: GEMINI_API_KEY -> gemini_api_key
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        #Boş bırakılan değişkenler (ör. "CHROMA_DIR=") varsayılana düşer
        env_ignore_empty=True,
        #.env'deki tanımadığımız değişkenler hata vermesin
        extra="ignore",
    )

    #.env'den değiştirilebilenler (.env.example'da listeli)
    #Key zorunlu değil: eval script'leri Gemini kullanmıyor; uygulama başlangıçta get_llm() ile kontrol ediyor
    gemini_api_key: SecretStr | None = None
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    chroma_dir: Path = Path("chroma_db_normalization")
    token_limit: int = 10000
    token_window_hours: int = 6
    usage_db_path: Path = Path("usage.db")
    llm_model: str = "gemini-2.5-flash"
    llm_temperature: float = 0.2
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    #Kimlik doğrulama yokken herkes silebileceği için varsayılan kapalı; açmak bilinçli bir karar olmalı
    allow_delete: bool = False

    #Kodda sabit tutulanlar (.env.example'da yok)
    #Değişirse DB yeniden kurulmalı
    embedding_model: str = "intfloat/multilingual-e5-base"
    collection_name: str = "langchain"
    chunk_size: int = 1200
    chunk_overlap: int = 300
    #Değişirse eval ile ölçülmeli
    retrieval_k: int = 3
    score_threshold: float = 0.72
    #Frontend'deki değerlerle aynı kalmalı (page.tsx MAX_UPLOAD_BYTES, ChatArea.tsx MAX_QUESTION_LENGTH)
    max_upload_mb: int = 20
    max_question_length: int = 500
    #Docker'daki bind mount'a bağlı
    upload_dir: Path = Path("uploaded_pdfs")

    @property
    def max_upload_bytes(self):
        return self.max_upload_mb * 1024 * 1024

    #Sadece boşluktan oluşan key de yok sayılır
    @field_validator("gemini_api_key", mode="before")
    @classmethod
    def strip_api_key(cls, value):
        if isinstance(value, str):
            return value.strip() or None
        return value

    #"debug" da kabul edilir; listede olmayan bir seviye uygulamayı başlatmaz
    @field_validator("log_level", mode="before")
    @classmethod
    def upper_log_level(cls, value):
        if isinstance(value, str):
            return value.strip().upper()
        return value

    #.env'de virgülle ayrılmış liste: "http://a,http://b" (pydantic-settings varsayılan olarak JSON bekliyor)
    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value):
        if isinstance(value, str):
            return [o.strip() for o in value.split(",") if o.strip()]
        return value

    #Göreli yollar proje köküne göre çözülür (Docker'da /app, Windows'ta proje klasörü); mutlak yollar olduğu gibi kalır
    @field_validator("chroma_dir", "usage_db_path", "upload_dir")
    @classmethod
    def resolve_from_root(cls, value):
        return PROJECT_ROOT / value

    #Sınır saate bağlı pencerelerle sıfırlanır (6 saatte: 00:00, 06:00, 12:00, 18:00); pencereler günü tam bölmeli
    @field_validator("token_window_hours")
    @classmethod
    def window_divides_day(cls, value):
        if value <= 0 or 24 % value:
            raise ValueError("TOKEN_WINDOW_HOURS 24'ü tam bölmeli (ör. 1, 2, 3, 4, 6, 8, 12, 24).")
        return value


#Ayarlar bir kez okunur; testler get_settings.cache_clear() ile yeniden okutabilir
@lru_cache
def get_settings():
    return Settings()
