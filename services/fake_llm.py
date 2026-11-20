import random
import re
from time import sleep
import httpx
from langchain_core.messages import AIMessage

#SAHTE LLM (LLM_MODE=fake, notes2.md Faz L)
#Gemini'nin yerine geçer; geri kalan akış (arama, reranker, BILGI_YOK kontrolü, süre logu, hata eşleme) gerçek çalışır
#Gerçek cevapla aynı biçimde AIMessage döner; hatalarda Gemini'nin fırlattığı httpx hatalarını fırlatır, böylece
#services/query.py classify_llm_error → WARNING log → 504/503 yolu aynen kullanılır

FAKE_PREFIX = "[SAHTE CEVAP]"
QUOTE_CHARS = 200

#format_document'ın ürettiği ilk <belge> bloğunun metni (etiketler parçadan zaten temizlenmiş)
_FIRST_DOCUMENT = re.compile(r"<belge[^>]*>\n(.*?)\n</belge>", re.DOTALL)


def fake_answer_text(documents):
    match = _FIRST_DOCUMENT.search(documents)
    quote = " ".join((match.group(1) if match else documents).split())
    if len(quote) > QUOTE_CHARS:
        quote = quote[:QUOTE_CHARS].rstrip() + "..."
    return f"{FAKE_PREFIX} Bu cevap Gemini'den gelmedi (LLM_MODE=fake). İlk kaynaktan alıntı: \"{quote}\""


#settings parametre olarak geliyor: testler services.query.get_settings'i değiştirince burası da aynı ayarı görür
def invoke_fake_llm(chain_input, settings):
    if random.random() < settings.fake_llm_error_rate:
        kind = settings.fake_llm_error_kind
        if kind == "mixed":
            kind = random.choice(("timeout", "connection"))
        if kind == "timeout":
            #Gerçekte her deneme zaman aşımına kadar bekler; thread de o kadar meşgul kalır
            sleep(settings.llm_timeout_seconds * settings.llm_max_attempts)
            raise httpx.ReadTimeout("Sahte zaman aşımı (LLM_MODE=fake)")
        raise httpx.ConnectError("Sahte bağlantı hatası (LLM_MODE=fake)")

    sleep(settings.fake_llm_delay_seconds)
    text = fake_answer_text(chain_input["documents"])
    #Karakter sayısından kaba tahmin (count_tokens'ın yedek tahminiyle aynı oran)
    input_tokens = (len(chain_input["documents"]) + len(chain_input["question"])) // 4
    output_tokens = len(text) // 4
    return AIMessage(
        content=text,
        usage_metadata={"input_tokens": input_tokens, "output_tokens": output_tokens,
                        "total_tokens": input_tokens + output_tokens},
        response_metadata={"finish_reason": "STOP"},
    )
