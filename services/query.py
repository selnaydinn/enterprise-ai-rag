import os
from langchain_ollama import ChatOllama
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from core.config import db , llm 

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
prompt = PromptTemplate(template=prompt_template, input_variables=["single_context", "question"])
qa_chain = prompt | llm | StrOutputParser()

# VECTOR SEARCH in DB
def search_in_db(user_query):
    print(f"\n...Vektör veritabanında arama yapılıyor: {user_query}...")
    #e5 modeli için gerekli olan 'query' ön ekini ekliyoruz
    optimized_query = f"query: {user_query}"
    results = db.similarity_search_with_relevance_scores(optimized_query, k=3)
    return results

#Optimized treshold değeri
SCORE_THRESHOLD = 0.72 

def prepare_chunks_with_metadata(user_input):  
    context_text = ""
    
    #Gelen sorguyla DBdeki metin parçalarını eşleştirdik
    matched_chunks = search_in_db(user_input)
    valid_chunks = []

    for idx, (doc, score) in enumerate(matched_chunks, 1):
        #Skor kontrolü
        if score < SCORE_THRESHOLD:
            print(f"Parça {idx} elendi (Embedding benzerlik skoru barajın altında: {round(score, 2)})")
            continue
            
        #Metadatayı çektik
        source_name = doc.metadata.get("source_file", "bilinmiyor")
        page = doc.metadata.get("page_number", "-")
        
        #LLMe metin parçalarını göndermeden önce metadata bilgisini de ekledik
        context_text += f"\n--- [DÖKÜMAN PARÇASI - KAYNAK: {source_name}, SAYFA: {page}] ---\n{doc.page_content}\n"
        
        valid_chunks.append({
            "source_name": source_name,
            "page": page,
            "score": score,
            "raw_text": doc.page_content
        })

    #Eğer barajı geçen hiçbir chunk kalmadıysa direkt uyarı dönüyoruz
    if not valid_chunks:
        return {
            "status": "Warning",
            "message": "Soru için 0 adet alakalı doküman parçası bulundu.",
            "data": "Lütfen dökümanlarımda olan bir soru girin. Aksi halde 3 alakasız girişimde 24 saat süreyle kullanımınız kısıtlanacaktır.",
            "resource" : [] ,
            "token_usage" : 0
        }

    
    print("Gemini API tetikleniyor, tüm döküman tek seferde analiz ediliyor...")
    
    #LLM tetiklendi
    final_llm_response = qa_chain.invoke({
        "single_context": context_text, 
        "question": user_input
    }).strip()

    #Token miktarı tahmini hesaplandı çünkü langchain üzerinden erişince bu bilgi siliniyormuş
    estimated_input_tokens = len(prompt_template + context_text ) // 4
    estimated_output_tokens = len(final_llm_response) // 4
    estimated_tokens = estimated_input_tokens + estimated_output_tokens

    #Tresholdu geçen chunkları birleştirdik
    final_paragraphs = []
    for chunk in valid_chunks:
        paragraph = {
            "source_name": chunk['source_name'],
            "page": chunk['page'],
            "score": round(chunk['score'], 2),
            "raw_text": chunk['raw_text']
        }
        final_paragraphs.append(paragraph)
    
    return {
        "status": "Success",
        "message": f"Soru için {len(final_paragraphs)} adet döküman kaynağı incelendi ve bütünsel cevap üretildi.",
        "data": final_llm_response,
        "resource": final_paragraphs,
        "token_usage" : estimated_tokens
    }