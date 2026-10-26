import logging
from functools import lru_cache
from langchain_core.prompts import PromptTemplate
from core.config import get_db, get_llm
from core.settings import get_settings

logger = logging.getLogger("EnterpriseAI")

# PROMPTING CHATBOT
prompt_template = """Sen sadece sana verilen döküman parçasına sadık kalarak cevap veren kurumsal bir asistansın.
Aşağıdaki döküman parçasını dikkatlice incele ve kullanıcının sorusuna yönelik dökümanda geçen bilgileri net ve kurumsal bir dille açıkla.

KURAL 1: İçerikte geçen öğrenci sayıları, kontenjanlar, kısıtlamalar ve süreler gibi tüm sayısal verileri KESİNLİKLE dökümanda yazdığı haliyle, değiştirmeden kullan.
KURAL 2: Eğer döküman parçasında kullanıcının sorusuna doğrudan cevap veren bir cümle veya sayı YOKSA, kesinlikle kafandan sayı uydurma veya metni zorlama. Sadece dökümanda net olarak ne görüyorsan onu aktar.

DÖKÜMAN PARÇASI:
{single_context}

KULLANICI SORUSU:
{question}

YAPAY ZEKA CEVABI:"""

#Chain yapısı oluşturduk
#StrOutputParser kullanmıyoruz; dönen AIMessage'daki usage_metadata'dan gerçek token sayısını okuyoruz
prompt = PromptTemplate(template=prompt_template, input_variables=["single_context", "question"])

#Zincir ilk soruda kuruluyor; LLM import anında oluşturulmasın
@lru_cache
def get_qa_chain():
    return prompt | get_llm()

#Gerçek token sayısı (Gemini'nin thinking token'ları dahil)
#usage_metadata gelmezse kota 0 sayılmasın diye karakter sayısından tahmin ediyoruz
def count_tokens(llm_message, prompt_text):
    usage = llm_message.usage_metadata
    if usage and usage.get("total_tokens"):
        return usage["total_tokens"]
    logger.warning("usage_metadata gelmedi, token sayısı tahmin ediliyor.")
    return (len(prompt_text) + len(llm_message.text)) // 4

# VECTOR SEARCH in DB
def search_in_db(user_query):
    #Soru metni şimdilik loglanıyor (KVKK açısından notes.md madde 21'de açık)
    logger.info(f"Vektör veritabanında arama yapılıyor: {user_query}")
    #e5 modeli için gerekli olan 'query' ön ekini ekliyoruz
    optimized_query = f"query: {user_query}"
    results = get_db().similarity_search_with_relevance_scores(optimized_query, k=get_settings().retrieval_k)
    return results

def prepare_chunks_with_metadata(user_input):
    context_text = ""
    #Optimized treshold değeri (core/settings.py)
    score_threshold = get_settings().score_threshold

    #Gelen sorguyla DBdeki metin parçalarını eşleştirdik
    matched_chunks = search_in_db(user_input)
    valid_chunks = []

    for idx, (doc, score) in enumerate(matched_chunks, 1):
        #Skor kontrolü
        if score < score_threshold:
            logger.debug(f"Parça {idx} elendi (benzerlik skoru barajın altında: {round(score, 2)})")
            continue
            
        #Metadatayı çektik
        source_name = doc.metadata.get("source_file", "bilinmiyor")
        page = doc.metadata.get("page_number", "-")
        
        #LLMe metin parçalarını göndermeden önce metadata bilgisini de ekledik
        context_text += f"\n--- [DÖKÜMAN PARÇASI - KAYNAK: {source_name}, SAYFA: {page}] ---\n{doc.page_content}\n"
        
        valid_chunks.append({
            "source_name": source_name,
            "page": page,
            "score": round(score, 2),
            "raw_text": doc.page_content
        })

    #Eğer barajı geçen hiçbir chunk kalmadıysa direkt uyarı dönüyoruz
    if not valid_chunks:
        return {
            "status": "Warning",
            "message": "Soru için 0 adet alakalı doküman parçası bulundu.",
            "data": "Sorunuzla ilgili yüklü dokümanlarda yeterli bilgi bulunamadı. Lütfen sorunuzu dokümanların kapsamına göre yeniden yazın.",
            "resource" : [] ,
            "token_usage" : 0
        }

    
    logger.info(f"Gemini API çağrılıyor ({len(valid_chunks)} parça tek prompt'ta).")
    
    #LLM tetiklendi
    chain_input = {
        "single_context": context_text,
        "question": user_input
    }
    llm_message = get_qa_chain().invoke(chain_input)

    #.text, content liste halinde gelse de düz metni verir (StrOutputParser'ın yaptığı gibi)
    final_llm_response = llm_message.text.strip()
    token_usage = count_tokens(llm_message, prompt.format(**chain_input))

    return {
        "status": "Success",
        "message": f"Soru için {len(valid_chunks)} adet döküman kaynağı incelendi ve bütünsel cevap üretildi.",
        "data": final_llm_response,
        "resource": valid_chunks,
        "token_usage" : token_usage
    }