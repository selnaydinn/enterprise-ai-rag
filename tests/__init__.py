import os

#Tüm testlerden önce: gerçek Gemini/Chroma/model ve ağ kullanımını engelleyen korumalar (tests/_guards.py)
from tests._guards import install

install()

#Yerel .env'de LLM_MODE=fake olsa da (Docker'da yük testi denemesi) testler gerçek modda çalışır; ortam değişkeni
#.env'nin önüne geçer. Sahte mod testleri modu kendileri açıyor (tests/test_fake_llm.py)
os.environ["LLM_MODE"] = "gemini"
