from datetime import datetime
from typing import Literal
from pydantic import BaseModel

#API yanıtlarının biçimi; endpoint'ler response_model ile bunlara uymak zorunda
#Eksik/yanlış tipte alan hata verir, modelde olmayan alanlar yanıta girmez
#Frontend'deki karşılıkları: rag-frontend/types/api.ts (ikisi birlikte güncellenmeli)


#Token kullanım durumu (services/usage.py get_status)
class UsageStatus(BaseModel):
    used: int
    limit: int
    resets_at: datetime
    limited: bool


#Cevapta kullanılan doküman parçası
class Source(BaseModel):
    source_name: str
    page: int
    score: float
    raw_text: str


#success: cevap üretildi · no_result: hiçbir parça barajı geçmedi, LLM çağrılmadı
class QueryResponse(BaseModel):
    status: Literal["success", "no_result"]
    message: str
    answer: str
    sources: list[Source]
    token_usage: int
    usage: UsageStatus


#filename: diske kaydedilen ad (backend ".PDF" uzantısını ".pdf" yapabiliyor)
class UploadResponse(BaseModel):
    filename: str
    total_pages: int
    total_chunks: int
    message: str


class DocumentItem(BaseModel):
    name: str


class DeleteResponse(BaseModel):
    name: str
    deleted_chunks: int


#Frontend'in hangi özellikleri göstereceği (ör. silme butonu); ayarlar backend'de tek yerde kalıyor
class FeaturesResponse(BaseModel):
    allow_delete: bool
