# Enterprise AI — Kod İnceleme Notları

> İnceleme tarihi: 2026-10-18 · Branch: `extra-features` · Kapsam: backend (FastAPI), frontend (Next.js), Docker, `eval/`, `tests/`
> Bu inceleme sırasında projede hiçbir dosya değiştirilmedi; yalnızca bu not dosyası eklendi.

GUNCEL: Madde 11 ve 14 yapıldı.
        Madde 11 incelendi, passage ön eki anlamlı bir retriaval kalitesi sağlamadı.
        Madde 14 teki 10 soruluk golden set 25 soruya çıkartıldı.  (tamamı meb_yonetmelik.pdf)(negatif soru yok)
        Chunking kalitesini iyileştirmek için pdflere boşluk karakteri eklendi. Bu işlem Normalization olarak adlandırıldı. Db bu işlemle tekrar oluşturuldu. Hit@3 23/25'ten 24/25'e çıktı. Buna karşılık evidence MRR 0.787'den 0.766'ya, evidence@1 de 17'den 16'ya hafifçe düştü. 

GUNCEL: 1,2,3,7,15 ve 17. maddeler yapıldı. 
        Artık path traversal önlemek için pdf ismi geçerli olanları alıyoruz.
        Farklı adla aynı pdf yüklerse 409(conflict) veriyor. 
        Aynı adla farklı pdf yüklerse yine 409 veriyor. 
        Gitignore'a "lf" formatı kondu. (daha git commite girmedi)
        20mb dosya boyut limiti var.
        Frontend artık `status`'a bakmadan dokümanı listeye eklemiyor.
        `<input type="file">`'ın değeri her yükleme sonrasında sıfırlanıyor. Hatalı yükleme olsa bile.

GUNCEL: Madde 5,6 ve 16 yapıldı.
        Madde 5 CORS ayarları güncellendi.
        Madde 6 `HTTPException(detail=str(e)) silindi. Mesajlar istemciye sızmıyor.
        Input alanında shift+enter ve alt satıra doğru genişleme aktif.
        500 character sınırı kondu.
        Madde 16, artık soru yanıtlanırken başka soru sorulamıyor.


GUNCEL: Madde 8,9,10 ve 19 yapıldı.
        Token limiti artık 6 saatlik aralıklarla sıfırlanıyor.
        Token sayımı tahminş değil gerçek usage ile yapılıyor.
        Yeni bir /usage endpointi var. Uygulamanın başında ve her token kullanımında + sıfırlanmada çalıştırılıyor.
        API'ın donmaması için dosya yüklemesi ayrı bir threadde yapılıyor. Dosya yüklenirken baska bir dosya yüklenemiyor.
        Sabit yollar artık ortam değişkeninden okunuyor.
---

## 1. Genel Bakış

PDF yönetmelikleri üzerinde soru-cevap yapan bir RAG uygulaması:

- **Ingestion** (`services/ingestion.py`): PDF → `PyPDFLoader` → sayfa bazlı `RecursiveCharacterTextSplitter` (1200/300 karakter) → `multilingual-e5-base` embedding → ChromaDB.
- **Query** (`services/query.py`): `query: ` ön ekli similarity search (k=3) → 0.72 skor barajı → tüm geçen parçalar tek prompt'ta Gemini 2.5 Flash'a.
- **API** (`main.py`): `/upload-pdf/`, `/query/`, `/documents/`, `/static-pdfs` (PDF'leri statik servis eder).
- **Frontend** (`rag-frontend/`): Üç panelli arayüz (doküman listesi, sohbet, PDF önizleme).
- **Eval** (`eval/`, henüz commit edilmemiş): 10 soruluk golden set ile salt-okunur retrieval ölçümü. Baseline: Hit@3 = 9/10, Hit@5 = 10/10.

### Güçlü yönler
- Kod küçük, okunabilir ve katmanlara ayrılmış (core / services / API / frontend).
- PDF hash ile tekrar yükleme kontrolü, deterministik chunk ID'leri, metadata'da kaynak dosya + sayfa numarası tutulması.
- Prompt'ta halüsinasyonu kısıtlayan kurallar ve kaynak gösterimi (frontend'de "Kullanılan Kaynaklar").
- `eval/evaluate_retrieval.py` çok özenli yazılmış: orijinal DB'yi kopyalayıp çalışıyor, SHA-256 ile değişmediğini doğruluyor, index bütünlüğü ve embedding tutarlılığı kontrolü yapıyor. Ölçmeden değiştirmeme yaklaşımı doğru.
- `core/embeddings.py` wrapper'ı ve testleri, `embed_query → embed_documents` tuzağını (subclass olsaydı `passage: query:` olurdu) bilinçli şekilde çözüyor.
- `.env` hiçbir zaman commit edilmemiş (git geçmişi kontrol edildi), `.gitignore` makul.

---

## 2. Öncelik Sırasıyla İyileştirilmesi Gerekenler

### 🔴 Kritik (Güvenlik / Veri bütünlüğü)

1. **Upload'da path traversal** — `main.py:64`
   `os.path.join(UPLOAD_DIR, file.filename)` kullanıcıdan gelen dosya adını doğrudan kullanıyor. `../../core/x.pdf` gibi bir ad, `uploaded_pdfs/` dışına yazabilir. Dosya adı `os.path.basename` + whitelist karakterlerle temizlenmeli ya da sunucu tarafında üretilmiş bir ad (ör. hash) kullanılmalı.

2. **Duplikasyon kontrolü dosya diske yazıldıktan SONRA yapılıyor** — `main.py:67-74`, `ingestion.py:32`
   - Aynı içerik farklı adla yüklenirse: DB "Reddedildi" der ama dosya `uploaded_pdfs/`'e kaydedilmiş olur → `/documents/` listesinde hayalet doküman görünür.
   - Aynı adla farklı içerik yüklenirse: eski PDF diskte üzerine yazılır, ama eski chunk'lar DB'de kalır ve aynı `source_file` adını gösterir → kaynak linkleri yanlış PDF'i açar, eski içerik cevaplara karışmaya devam eder.
   - Öneri: önce byte'ları hash'le → DB'de kontrol et → sonra yaz. Aynı ad çakışmasında reddet veya versiyonla.

3. **`.dockerignore` yok** — `Dockerfile:11` (`COPY . .`)
   `.env` (Gemini API key), `venv/` (Windows venv'i, Linux'ta işe yaramaz), `rag-frontend/node_modules`, `.next`, `chroma_db`, `.git` image'a kopyalanıyor. Image bir registry'ye push edilirse API key sızar. Image boyutu da gereksiz büyür.

4. **Kimlik doğrulama / yetkilendirme yok**
   Herkes PDF yükleyebilir, sorgu atıp ortak token kotasını tüketebilir, `/static-pdfs/` altındaki tüm dosyaları indirebilir. "Enterprise" iddiası için en azından API key / oturum katmanı gerekli. Frontend'deki kullanıcı ("Selin Aydın", `selin@company.com`) ve çıkış butonu sabit/göstermelik (`LeftPanel.tsx:119-131`).

5. **CORS çok geniş** — `main.py:27-33`
   `allow_origins=["*"]` + `allow_credentials=True` birlikte kullanılmamalı. Origin listesi ortam değişkeninden okunmalı (ör. `http://localhost:3000`).

6. **Hata detayları istemciye sızıyor** — `main.py:54, 81, 119`
   `HTTPException(detail=str(e))` iç hata mesajlarını (dosya yolları, kütüphane hataları, API hataları) kullanıcıya gösteriyor. Loglanmalı, istemciye genel mesaj dönülmeli.

7. **Upload boyut limiti yok** — `main.py:68`
   `await file.read()` tüm dosyayı RAM'e alıyor; container 3 GB limitli. Maksimum boyut ve sayfa sayısı kontrolü eklenmeli; ayrıca `.pdf` uzantısı yerine içerik (magic bytes `%PDF`) kontrol edilmeli. `endswith('.pdf')` büyük harfli `.PDF`'i de reddediyor.

### 🟠 Yüksek (Doğruluk / Davranış hataları)

8. **Token limiti mantığı hatalı ve yanıltıcı** — `main.py:14-15, 86-107`
   - Global değişken: tüm kullanıcılar için ortak, process restart'ta sıfırlanır, birden fazla worker'da tutarsız, eşzamanlı isteklerde race condition.
   - Hata mesajı "24 saat sonra sıfırlanacaktır" diyor ama **sıfırlama kodu yok**. Limit dolunca uygulama restart'a kadar kilitlenir.
   - `query.py:69`'daki "3 alakasız girişimde 24 saat kısıtlanacak" uyarısının da karşılığı yok.
   - Limit kontrolü istekten önce yapılıyor; son istek limiti aşabilir, sonra değer `TOKEN_LIMIT`'e kırpılıyor (gerçek tüketim kayboluyor).
   - Öneri: kullanıcı/IP bazlı, zaman pencereli bir sayaç (Redis veya SQLite), limit değerini config'den alma.

9. **Token sayımı tahmini ve yanlış** — `query.py:83-86`
   `len(text)//4` İngilizce için kabaca bir kural; Türkçe için daha da sapar ve `{question}` metni hesaba katılmıyor. Yorumdaki "langchain üzerinden erişince siliniyor" tespiti aslında `StrOutputParser` kaynaklı: zinciri `prompt | llm` olarak bırakıp dönen `AIMessage.usage_metadata`'dan gerçek input/output token sayıları okunabilir.

10. **Event loop bloklanıyor** — `main.py:58, 85`
    `async def` endpoint'ler içinde senkron ve ağır işler çağrılıyor (`process_pdf` → CPU-bound embedding, `qa_chain.invoke` → ağ çağrısı). Bir kullanıcı PDF yüklerken tüm API donar. Çözüm: endpoint'leri `def` yapmak (FastAPI threadpool'da çalıştırır), `run_in_threadpool` veya `qa_chain.ainvoke` kullanmak; büyük PDF'ler için arka plan görevi + durum endpoint'i.

11. **E5 ön ekleri asimetrik / wrapper henüz bağlanmamış** — `query.py:30`, `ingestion.py`, `core/embeddings.py`
    - Sorgular `query: ` ile, chunk'lar **ön eksiz** embed ediliyor (baseline adı `baseline_e5_noprefix` bunu doğruluyor). E5 modeli chunk'larda `passage: ` bekler.
    - `core/embeddings.py` yazılmış ama `core/config.py`'de kullanılmıyor. Bağlanırsa mevcut `chroma_db` **mutlaka yeniden index'lenmeli**, aksi halde sorgu ve dokümanlar farklı uzaylarda kalır.
    - Bağlandıktan sonra `query.py:30`'daki manuel ön ek kaldırılmalı (wrapper çift ön eki engelliyor ama tek kaynaktan yönetmek daha temiz).
    - Değişiklik sonrası `eval/` ile yeni sonuç alınıp baseline ile kıyaslanmalı. 

12. **Skor barajı (0.72) kalibre edilmemiş** — `query.py:35`
    E5 skorları dar bir aralıkta yoğunlaşır (eval'de tüm isabetler 0.80–0.86). Golden set'te yalnızca cevaplanabilir sorular var; **cevabı olmayan / alakasız negatif sorular** eklenmeden barajın işe yarayıp yaramadığı bilinemez. Ayrıca ön ek değişikliği skor dağılımını kaydıracağı için baraj yeniden ayarlanmalı.

13. **Retrieval kalitesi** — `query.py:31`, `ingestion.py:46-50`
    - `k=3` dar: q09'da doğru parça 5. sırada → LLM'e ulaşmıyor. Daha geniş aday (k=10-20) + reranker (ör. `bge-reranker-v2-m3` çok dilli) değerlendirilebilir.
    - Chunking sayfa sayfa yapılıyor; sayfa sınırını aşan maddeler bölünüyor (golden set notu q02 bunu belirtiyor). Yönetmelikler için "Madde" başlıklarına göre bölme (structure-aware chunking) çok daha uygun olur.
    - Karakter bazlı 1200/300 yerine token bazlı boyutlandırma düşünülebilir (e5-base'in 512 token sınırı var; uzun chunk'lar sessizce kırpılabilir — kontrol edilmeli).
    - Hibrit arama (BM25 + vektör) madde numarası / sayısal sorgularda faydalı olur.

14. **Golden set dar** — `eval/golden_set.jsonl`
    10 sorunun tamamı `meb_yonetmelik.pdf`'ten. Diğer 3 PDF (Acil Sağlık, Kan Merkezleri, Sözleşmeli Öğretmenlik) hiç ölçülmüyor; çok dokümanlı ortamda yanlış dokümandan getirme (cross-document confusion) test edilmiyor. Soru sayısı artırılmalı ve negatif örnekler eklenmeli.

15. **Frontend: tekrar yüklenen dosya listeye yine ekleniyor** — `page.tsx:84-93`
    Backend duplikasyonda HTTP 200 + `"status": "Reddedildi"` dönüyor; frontend `status`'a bakmadan dokümanı listeye ekliyor → listede aynı dosya iki kez. Backend bu durumda `409 Conflict` dönmeli ve/veya frontend `status`'u kontrol etmeli.

16. **Frontend: eşzamanlı gönderim koruması yok** — `ChatArea.tsx:173`
    Enter'a hızlı basınca `isLoading` kontrolü olmadığı için birden fazla istek gider (ve token kotası harcanır). Ayrıca Türkçe/IME girişlerinde `e.nativeEvent.isComposing` kontrolü yapılmalı.

17. **Aynı dosya tekrar seçilince upload tetiklenmiyor** — `ChatArea.tsx:165`
    `<input type="file">`'ın değeri sıfırlanmadığı için hata sonrası aynı dosyayı tekrar seçmek `onChange` üretmez (`e.target.value = ''`).

### 🟡 Orta (Mimari / Bakım kolaylığı)

18. **`core/config.py` import anında ağır yan etkiler üretiyor**
    Import edildiği anda model yükleniyor, DB'ye bağlanılıyor, Gemini client oluşturuluyor. Test yazmayı zorlaştırıyor (eval script'i bu yüzden sabitleri kopyalamak zorunda kalmış, bkz. `evaluate_retrieval.py:23-27`). Öneri:
    - `pydantic-settings` ile tek bir `Settings` sınıfı (model adı, chroma yolu, k, threshold, token limiti, CORS origin'leri, LLM modeli).
    - Model/DB/LLM nesnelerini `lru_cache`'li factory fonksiyonları veya FastAPI `lifespan` + dependency injection ile oluşturmak.
    - Eval ve uygulama aynı config'i okumalı; şu an değerler iki yerde, elle senkron tutuluyor.

19. **Sabit kodlanmış yollar ve URL'ler**
    - `core/config.py:14` → `chroma_dir = "/app/chroma_db"`: Docker dışında (ör. Windows'ta) `C:\app\chroma_db` oluşturur, projedeki `chroma_db/` kullanılmaz. Ortam değişkeni + proje köküne göre yol kullanılmalı.
    - Frontend'de backend adresi iki farklı şekilde sabit: `api.ts:1` `127.0.0.1:8000`, `page.tsx:195` `localhost:8000`. `NEXT_PUBLIC_API_URL` ile tek yerden yönetilmeli.
    - PDF URL'inde dosya adı `encodeURIComponent` ile kodlanmıyor (`page.tsx:195`); boşluk/Türkçe karakter/`#` içeren adlar sorun çıkarır.

20. **API key doğrulanmıyor** — `core/config.py:9-10`
    Key yoksa bile "Key etkinleştirildi." yazdırılıyor; hata ilk sorguda ortaya çıkıyor. Başlangıçta fail-fast yapılmalı. `.env`'de `GEMINI_API_KEY =` (eşittir öncesi boşluk) var; python-dotenv bunu tolere ediyor ama Docker `env_file` bazı sürümlerde anahtarı boşluklu okuyabilir — `GEMINI_API_KEY=...` formatına çekilmeli. Ayrıca bir `.env.example` dosyası eklenmeli.

21. **`print` ve `logging` karışık kullanılıyor**
    `config.py`, `ingestion.py`, `query.py` `print` (emoji'li) kullanıyor; `main.py` `logging`. Hepsi `logging` modülüne geçirilmeli; kullanıcı sorularının loglanması (`query.py:28`) KVKK açısından da düşünülmeli.

22. **Deprecated LangChain importları**
    `langchain_community.embeddings.HuggingFaceEmbeddings` ve `langchain_community.vectorstores.Chroma` deprecated. Yerine `langchain-huggingface` ve `langchain-chroma` paketleri kullanılmalı.

23. **Bağımlılıklar** — `requirements.txt`
    - Hiçbir versiyon sabitlenmemiş → build'ler tekrarlanabilir değil, LangChain gibi hızlı değişen kütüphanelerde kırılma riski yüksek. Eval sonuçları zaten `chromadb 1.5.9`, `sentence-transformers 5.6.0` ile alınmış; bunlar pinlenmeli (veya `pip-tools`/`uv` lock dosyası).
    - Kodda kullanılmayan paketler: `langchain-ollama` (sadece kullanılmayan bir import var), `pyspellchecker`, `PyMuPDF`, `tiktoken`, `pillow`. Kaldırılmalı veya kullanım amacı belgelenmeli.
    - Kullanılan ama listede olmayanlar: `python-dotenv`, `langchain-text-splitters` (dolaylı geliyor; açıkça yazılmalı).
    - `torch` CPU-only wheel ile kurulursa image boyutu ciddi şekilde küçülür.

24. **Kullanılmayan kod**
    - `services/query.py:1-2` → `os`, `ChatOllama` kullanılmıyor.
    - `query.py:88-97` → `valid_chunks` zaten aynı alanları içeriyor; tekrar kopyalayan döngü sadece skoru yuvarlıyor, sadeleştirilebilir.
    - `api.ts:56-62` boş satırlar; GET isteğinde gereksiz `Content-Type` header'ı.

25. **Docker yapılandırması geliştirme moduna göre**
    - `--reload` + `.:/app` bind mount production için uygun değil; dev/prod için ayrı compose dosyaları (veya profiller) olmalı.
    - Image root kullanıcıyla çalışıyor; non-root user eklenmeli.
    - Embedding modeli her container başlangıcında indiriliyor olabilir; HF cache için volume veya build sırasında indirme.
    - `chroma_db` için ayrı bir named volume yok (bind mount sayesinde çalışıyor, ama bind mount kaldırılırsa veri kaybolur).
    - `version: '3.8'` alanı artık obsolete.
    - `dns: 8.8.8.8` sabit kodlu; kurumsal ağlarda (iç DNS, proxy) sorun çıkarabilir.
    - Frontend compose'a dahil değil; tek komutla tüm sistem ayağa kalkmıyor.
    - `healthcheck` tanımlı değil.

26. **API tasarımı**
    - Response şemaları Pydantic model değil, serbest dict. `status` değerleri tutarsız: `"Success"`, `"Warning"`, `"Reddedildi"`, `"Hata"` (dil ve büyük/küçük harf karışık). Enum + `response_model` kullanılmalı.
    - `page` backend'de `int`, frontend tipinde `string` (`page.tsx:14`).
    - `total_token_usage` gereksiz yere nested obje (`{"total_tokens": N}`).
    - Doküman silme / yeniden index'leme endpoint'i yok. Bir PDF'i sistemden çıkarmanın tek yolu DB'yi elle silmek.
    - `QueryRequest.question` için uzunluk sınırı yok (`Field(min_length=1, max_length=...)`).
    - Versiyonlama yok (`/api/v1/...`).

27. **Prompt / LLM katmanı**
    - Kullanıcı sorusu ve PDF içeriği doğrudan prompt'a gömülüyor → prompt injection'a açık (yüklenen bir PDF'teki "önceki talimatları yok say" metni). Sistem/kullanıcı mesajı ayrımı (`ChatPromptTemplate` ile system rolü) ve içerik sınırlayıcıları (delimiter) kullanılmalı.
    - Prompt'ta "döküman parçası" tekil, ama birden fazla parça gönderiliyor. Cevapta hangi parçaya dayanıldığının (citation) istenmesi kaynak doğruluğunu artırır.
    - LLM çağrısında timeout / retry / hata sınıflandırması yok (Gemini 429/503 → kullanıcıya 500 + ham hata).
    - Yanıt streaming değil; uzun cevaplarda kullanıcı bekliyor.
    - README'deki "multi-turn memory" iddiası kodda karşılığı yok — her soru bağımsız.

### 🟢 Düşük (Frontend UX / Kod kalitesi)

28. `Message` ve `DocumentItem` arayüzleri `page.tsx`, `ChatArea.tsx`, `LeftPanel.tsx`'te tekrarlanıyor → `types/` altında tek bir yerde tanımlanmalı. API yanıtları da tiplenmeli (`askQuestion` şu an `any` dönüyor).
29. `catch (error: any)` yerine `unknown` + tip daraltma.
30. Upload progress bar sahte (`setInterval` ile %90'a kadar artıyor). Gerçek ilerleme yoksa belirsiz (indeterminate) bir spinner daha dürüst olur.
31. Kaynak butonuna tıklanınca PDF açılıyor ama ilgili sayfaya gitmiyor; iframe URL'ine `#page=${res.page}` eklenerek doğrudan sayfaya atlanabilir. Aynı dokümandan gelen birden fazla kaynak aynı rozeti tekrar ediyor.
32. LLM cevabı düz metin olarak basılıyor; Gemini genelde markdown döndürür → `**`, `-` işaretleri ham görünür. Bir markdown renderer (sanitize edilerek) kullanılmalı.
33. Token göstergesi `/ 10K` sabit kodlu; bir hata mesajından sonra son asistan mesajında `total_token_usage` olmadığı için sayaç 0'a düşüyor. Token durumu ayrı bir state'te tutulmalı (veya ayrı bir `/usage` endpoint'i).
34. Dil tutarsızlığı: arayüzde Türkçe ve İngilizce karışık ("Ask a question...", "No documents found.", "Search Among Company Files" vs. Türkçe mesajlar). `layout.tsx`'te `lang="en"`, title "Create Next App", description "Generated by create next app" — güncellenmeli. Font subset'inde `latin-ext` yok (Türkçe karakterler için gerekli olabilir).
35. Listelerde `key={index}` kullanılıyor; doküman listesinde `doc.name` daha uygun.
36. Erişilebilirlik: doküman listesi öğeleri tıklanabilir `div` (klavyeyle seçilemiyor), ikon butonlarında `aria-label` yok, mesaj alanında `aria-live` yok.
37. Gönder butonu metin boşken tamamen kayboluyor; disabled görünüm daha öngörülebilir.

---

## 3. Test, Kalite ve Süreç

- **Test kapsamı çok düşük:** yalnızca `E5Embeddings` wrapper'ı test ediliyor. Eksikler:
  - API testleri (`fastapi.testclient` + mock DB/LLM): upload doğrulamaları, path traversal, duplikasyon, token limiti, boş sonuç senaryosu.
  - `prepare_chunks_with_metadata` için baraj/filtreleme birim testleri (LLM mock'lanarak).
  - `process_pdf` için küçük bir fixture PDF ile metadata/ID testleri.
  - Frontend için hiç test yok.
- **Eval'i CI'a bağlamak:** retrieval metrikleri belirli bir eşiğin altına düşerse uyarı veren bir regresyon kontrolü.
- **CI/CD yok:** GitHub Actions ile lint (`ruff`, `eslint`), type check (`mypy`/`tsc`), test çalıştırma.
- **Linter/formatter yok (backend):** `ruff` + `ruff format` önerilir. Yorum stili (`#Yorum` boşluksuz) PEP 8'e uymuyor; tutarlılık açısından düzeltilebilir.
- **Git:** `core/embeddings.py`, `eval/`, `tests/` henüz commit edilmemiş. Commit geçmişinde anlamlı mesajlar yok (çoğu "Update README.md"). Küçük, açıklayıcı commit'ler önerilir.
- `eval/results/*.json` dosyalarının commit edilip edilmeyeceğine karar verilmeli (baseline'ı versiyonlamak faydalı; ama `golden_set` yolu Windows ayırıcısıyla `eval\\golden_set.jsonl` kaydedilmiş — `as_posix()` kullanılmalı).

---

## 4. Dokümantasyon

- README'deki iddialar kodla uyuşmuyor:
  - "modular **microservices** architecture" → tek bir FastAPI servisi var (monolit + ayrı frontend).
  - "**secure** LLM orchestration", "can run locally in a secure environment" → veriler bulut tabanlı Gemini API'ye gönderiliyor, auth yok.
  - "memory structures for multi-turn conversations" → yok.
  - "reduces tokenization costs" → ölçülmüş bir iddia değil.
- README'deki `bash` kod bloğu kapatılmamış (sonraki satırlar kod olarak render ediliyor).
- Eksik bölümler: `.env` değişkenleri listesi, API endpoint açıklamaları, eval'in nasıl çalıştırılacağı, testlerin nasıl çalıştırılacağı (`python -m unittest discover tests`), mimari diyagram, bilinen kısıtlar.
- `rag-frontend/README.md` hâlâ create-next-app şablonu.

---

## 5. Önerilen Yol Haritası

**Adım 1 — Güvenlik ve hızlı düzeltmeler (küçük, riskli olmayan)**
1. Dosya adı temizleme (path traversal) + boyut limiti + hash kontrolünü yazmadan önce yapma.
2. `.dockerignore` ekleme.
3. CORS origin'lerini kısıtlama, hata detaylarını gizleme.
4. Kullanılmayan import/paketleri temizleme, versiyonları pinleme.
5. Frontend'de duplikasyon `status` kontrolü, çift gönderim koruması.

**Adım 2 — Yapısal iyileştirme**
6. `Settings` + lazy factory'ler ile `core/config.py` refactor'u (eval ve uygulama aynı config'i kullansın).
7. Endpoint'leri bloklamayan hale getirme; upload'u arka plan görevine taşıma.
8. Token limitini kalıcı, kullanıcı bazlı ve zaman pencereli hale getirme; gerçek `usage_metadata` kullanma.
9. Pydantic response modelleri, tutarlı `status` enum'u, doküman silme endpoint'i.
10. API testleri + CI.

**Adım 3 — RAG kalitesi (her adımı eval ile ölçerek)**
11. Golden set'i genişletme (tüm PDF'ler + negatif sorular).
12. `E5Embeddings`'i bağlayıp yeniden index'leme → baseline ile kıyaslama.
13. Skor barajını negatif örneklerle yeniden kalibre etme.
14. Madde bazlı chunking, daha geniş aday + reranker, hibrit arama denemeleri.

**Adım 4 — Ürünleşme**
15. Kimlik doğrulama, doküman bazlı yetki, audit log.
16. Streaming yanıt, markdown render, kaynakta sayfaya atlama, çok turlu sohbet.
17. Production Docker yapılandırması (non-root, reload yok, healthcheck, frontend dahil compose).
