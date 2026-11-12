# Enterprise AI — Production Hazırlığı: Mimari Analiz

> İlk analiz: 2026-11-11 · Son güncelleme: 2026-11-12
> Bu dosya production yol haritası ve tek açık iş listesi. `notes.md` 2026-11-12'de kapandı; açık maddeleri (#13, #25, #43) aşağıdaki fazlara taşındı.

## Yapılacaklar

Ayrıntılar (dosyalar, riskler, ek RAM) "Yol haritası" tablosunda; gerekçeler §1–17'de.
Önerilen sıra: 0b → 1 → 2 → L → 3 → 4 → 5 → (6, 7, 8...). B ve B2 yayına alma kararıyla öne çekilir; R ve S bağımsızdır.

**Faz 0b: Log temizliği**
- ✅ Upload logunda tam yol yerine yalnızca dosya adı (`main.py`, test: `test_upload_logs_name_not_full_path`)

**Faz 1: Structured logging (§7)**
- ✅ JSON log formatı (`LOG_FORMAT=text|json`, varsayılan text; Docker'da JSON Faz 2'nin prod compose'unda açılacak)
- ✅ Request ID middleware ve `X-Request-ID` header'ı (500'de de; istek sonu özet satırı)
- ✅ uvicorn loglarını aynı formata bağlama (uvicorn'un erişim satırı da kalıyor, kullanıcı kararı)

**Faz 2: Health ve Docker (§10, §11, §17; notes.md #25)**
- ❌ Önce ölçüm: Docker yavaşlığının kaynağını bulmak (§17, deneme A–D, DB kopyasıyla)
- ❌ `/health/live` ve `/health/ready` endpoint'leri
- ❌ Docker dev/prod ayrımı (`--reload` ve `.:/app` yalnızca dev override'da)
- ❌ Docker healthcheck
- ❌ Graceful shutdown
- ❌ Container'da root olmayan kullanıcı
- ❌ `version` alanını silme, sabit `dns` ayarını gözden geçirme
- ❌ Ölçüme göre: DB'yi Docker volume'una almak ve/veya CPU sınırını ayarlamak
- ❌ Sonra tekrar ölçüm (Faz 2 öncesi ve sonrası karşılaştırması)

**Faz L: Sahte LLM modu ve yük testi (§16)**
- ❌ `LLM_MODE=fake`: Gemini yerine gecikmeli sahte cevap, isteğe bağlı sahte hata
- ❌ Sahte modda token'lar gerçek kullanım sınırını tüketmesin
- ❌ Sahte mod açıkken açılışta WARNING; varsayılan her zaman `gemini`
- ❌ Yük testi scripti (golden + negatif set soruları, ayarlanabilir eşzamanlılık)

**Faz 3: Sentry backend (§6)**
- ❌ Sentry kurulumu ve hassas veri temizleyici (scrubber)
- ❌ Beklenen / beklenmeyen hata ayrımı

**Faz 4: Metrikler (§8)**
- ❌ `/metrics` endpoint'i (Prometheus)

**Faz 5: Circuit breaker (§10)**
- ❌ Gemini circuit breaker, readiness'e bağlı

**Faz 6: Grafana (§8)**
- ❌ Prometheus + Grafana compose profili ve dashboard

**Faz 7: Tracing (§9)**
- ❌ OpenTelemetry + Jaeger

**Faz 8: Frontend dayanıklılığı**
- ❌ `X-Request-ID` gönderme ve hata mesajında gösterme
- ❌ Fetch timeout (AbortController)
- ❌ `error.tsx` hata sayfası
- ❌ Beklenen hatalar (503, 504, 429) `console.error` yerine `console.warn` veya hiç; yalnızca beklenmeyenler `console.error` (dev modda Next.js hata rozeti/penceresi gürültüsü, `Workspace.tsx:253`)
- ❌ Her cevabı konsola yazan `console.log("LLM frontende yanıtını döndü.", result)` satırını kaldırma (`Workspace.tsx:240`)
- ❌ PDF önbelleği: `/static-pdfs/{name}` cevabına `Cache-Control: private, no-cache` (`main.py` `read_pdf`) + test. Bugün tarayıcı PDF'i önbellekten açıyor, istek backend'e gitmiyor ve oturum kontrol edilmiyor (çıkıştan sonra da açılabilir). Sonrasında her açılışta istek gider, değişmemişse `304` döner. Faz B'den (internete açılmadan) önce yapılmalı

**Faz 9: Sentry frontend**
- ❌ `@sentry/nextjs` (opsiyonel)

**Faz 10: Frontend container'ı**
- ❌ Next.js standalone container'ı compose'a

**Faz B: Bulut hazırlığı (§15)**
- ❌ Caddy ile HTTPS
- ❌ Rate limit
- ❌ Sunucu firewall'u (yalnızca 22, 80, 443)
- ❌ Proxy header'ları (doğru istemci IP'si)
- ❌ Prod CORS ve `ALLOW_DELETE`

**Faz B2: Deploy (§15)**
- ❌ İmajı GHCR'a push eden workflow
- ❌ Sunucu kurulumu
- ❌ Veriyi sunucuya kopyalama
- ❌ Yedekleme

**Faz R: Retrieval kalitesi (notes.md #13)**
- ❌ q23 (vektör araması doğru parçayı kaçırıyor)
- ❌ Hibrit arama denemesi (anahtar kelime + vektör)
- ❌ Deneme C (Gemini kotası uygunken)

**Faz S: Streaming (notes.md #43)**
- ❌ Cevabın parça parça gelmesi (ayrı endpoint, mevcut `/query` bozulmadan)

**Faz 11: Öğrenme denemesi (opsiyonel)**
- ❌ Chroma server mode + ayrı ingestion worker (DB kopyasında)

**Verilecek kararlar**
- ❌ Sentry için SaaS ücretsiz plan mı? (Self-hosted ~4 GB+ RAM istiyor, önerilmez)
- ❌ OpenTelemetry opsiyonel mi (`requirements-otel.txt`), ana `requirements.txt`'e mi?
- ❌ Bulut sağlayıcı ve bölge (KVKK için AB bölgesi), makine boyutu (4 vCPU / 8 GB)
- ❌ Frontend nerede: aynı VPS'te container mı, Vercel gibi statik host mu?
- ❌ Domain (Caddy ile HTTPS için gerekli)


GUNCEL 1: Loglara request ID verildi
          Loglar json moduna çevrilebiliyor artık.(bağlanacak uygulamalar için)

## Yapılanlar

| Tarih | İş |
|---|---|
| 2026-11-11 | Mikroservise bölmeme kararı (modüler monolit); tek kullanıcılı giriş (notes.md #4) ve auth'lu `/static-pdfs/{name}` endpoint'i; upload rollback (Faz 0a, notes.md #45); boş DB mesajı (#44); frontend UX (#30–#37, #32 markdown); Next.js 16.4.6 güvenlik güncellemesi. Soru metni bilerek INFO logda kalıyor (kullanıcı kararı); Sentry'ye gidip gitmeyeceği Faz 3'te ayrıca karar |
| 2026-11-12 | Negatif set testi yalnızca eval PDF'lerine bakıyor; Dependabot (`.github/dependabot.yml`, torch hariç); pip-audit (`requirements-dev.txt`, `docs/development.md`); pypdf 6.14.0 → 6.21.4 (38 DoS açığı; çıkarılan metin 797 chunk'ta aynı, 13'ünde yalnız boşluk farkı); Docker portu yalnızca localhost'a (`127.0.0.1:8000:8000`); `notes.md` kapatıldı |
| 2026-11-12 | Faz 0b (upload logunda yalnızca dosya adı); Faz 1: `core/log_setup.py` (text/JSON biçim, request ID, istek sonu özet satırı, uvicorn logları aynı biçimde), `LOG_FORMAT`, CORS `expose_headers`, 500'de `X-Request-ID`; `tests/test_log_setup.py` |


Kalan pip-audit bulguları bilerek açık: chromadb (yalnızca sunucu modu, biz gömülü kullanıyoruz), torch (`torch.jit.script`, kullanılmıyor), setuptools (yalnızca paket kurulumu/derleme sırasında).

---

## Özet (TL;DR)

- **Mikroservise bölmeyi önermiyorum.** Proje temiz katmanlı bir *modüler monolit*. Ingestion ve query aynı embedding modelini (e5-base, ~1.1 GB RAM) ve aynı gömülü (embedded) Chroma'yı kullanıyor. Bölmek, modeli iki kez yüklemek (+1.1–1.6 GB) ve Chroma'yı ayrı bir sunucu container'ına taşımak demek. 3 GB'lık container sınırında bu kaldırılamaz, karşılığında da gerçek bir kazanç yok.
- **Asıl değer gözlemlenebilirlikte (observability):** request ID'li JSON loglar → Sentry (backend) → `/metrics` (Prometheus) → health/readiness → Gemini için circuit breaker → OpenTelemetry (opsiyonel).
- **Ek RAM (önerilen yol):** backend'e +~50–80 MB. Monitoring stack'i (Prometheus + Grafana + Jaeger) ancak `profiles` ile istenince açılıyor ve ~350–500 MB ekliyor.
- **Bulut:** Hedef, container'ları bir **VPS'te Docker Compose** ile çalıştırmak (§15). İnternete açılmadan önce Faz B tamamlanmalı.

---

## 1. Mevcut mimari ve istek akışı

```
Next.js (host'ta, compose'da değil) ──fetch──► FastAPI (tek container, tek uvicorn worker)
                                                ├─ main.py           endpoint'ler, validation, UPLOAD_LOCK, hata eşleme
                                                ├─ core/settings.py  pydantic-settings (.env)
                                                ├─ core/config.py    lru_cache'li singleton: LLM, e5, reranker, Chroma
                                                ├─ services/ingestion.py + chunking.py
                                                ├─ services/query.py retrieval + rerank + prompt + Gemini
                                                └─ services/usage.py SQLite token sayacı (usage.db)
                                               Disk: chroma_db_normalization/ (embedded PersistentClient), uploaded_pdfs/, usage.db
                                               Dış: Gemini API (HTTPS, google-genai → httpx)
```

**Query:** `POST /query/` (sync `def`, threadpool'da) → `get_status()` limit kontrolü → `search_in_db` (e5 query embedding + Chroma HNSW, k=10) → e5 eşiği 0.72 → reranker (mMiniLM, eşik −2.0) → ilk 3 parça → `<belge>` prompt → Gemini (20 sn × 2 deneme, retry kütüphanede) → BILGI_YOK / boş / yarım cevap ayrımı → `record_usage` → `QueryResponse`.

**Upload:** `POST /upload-pdf/` → ad, boyut ve `%PDF-` kontrolü → md5 hash → `UPLOAD_LOCK` → duplikasyon kontrolü (409) → diske yazma → `process_pdf` threadpool'da (PyPDF → normalize → split → 32'lik batch'lerle `add_documents`; ID = `h_{hash}_p{page}_c{idx}`, deterministik). Hata olursa o PDF'in chunk'ları geri alınır ve dosya silinir.

**Diğer:** `/auth/login`, `/auth/logout`, `/auth/me`; `GET /documents/`, `/usage/`, `/features/`, `DELETE /documents/{name}` (aynı kilitle), `GET /static-pdfs/{name}` (auth'lu), `GET /` (sabit cevap; bağımlılıkları kontrol etmiyor). `/`, login ve logout dışındaki her endpoint oturum ister.

**Hata modeli (iyi durumda):** kullanıcı hataları 4xx; Gemini'nin geçici hataları `LLMUnavailableError` → 503/504; beklenmeyen hatalar genel 500 dönüyor, stack trace kullanıcıya gitmiyor. Başlangıçta key, DB ve modeller yüklenip warm-up yapılıyor (fail-fast).

## 2. Monolit mi, modüler mi?

**Modüler monolit.** Katmanlar net: HTTP (main) / config / domain servisleri / persistence. Servisler HTTP'den habersiz, test edilebilir ve exception tabanlı. Tek deployment birimi, tek process ve paylaşılan bellek içi modeller var. `main.py` henüz router'lara bölmeyi gerektirecek kadar büyük değil.

## 3–5. Mikroservis değerlendirmesi

| Aday servis | Ayrılmalı mı? | Gerekçe |
|---|---|---|
| API Gateway | **Hayır** | Tek backend var; yönlendirilecek bir şey yok. FastAPI zaten validation yapıyor. Auth gelince de aynı uygulamada middleware/dependency olur. |
| Ingestion | **Şimdilik hayır** | e5 modeli gerekli (+1.1 GB). Embedded Chroma'ya iki process'ten yazmak güvenli değil (SQLite + HNSW dosyaları) ve Chroma server container'ı gerektirir (+200–400 MB). Tek kazanç, uzun bir upload'ın query'leri CPU'da yavaşlatmaması; bu da `cpus` kotasıyla sınırlı. |
| Query (retrieval + rerank) | **Hayır** | Ingestion ile aynı modeli ve DB'yi paylaşıyor; ayrılırsa veri sahipliği belirsizleşir. |
| Generation | **Hayır** | Gemini'ye yapılan ince bir HTTP çağrısı. Ayrı servis olursa bir ağ adımı, bir serileştirme ve bir hata noktası daha ekler. Failure isolation zaten `LLMUnavailableError` ve circuit breaker ile sağlanabilir. |
| Token usage | **Hayır** | Query yolunda senkron ve 2 sorguluk bir işlem. Ayrılırsa her soruya 2 ağ çağrısı eklenir ve tutarlılık sorunu çıkar (Gemini cevap verdi, usage servisi kapalıysa ne olacak?). |

**Gereksiz karmaşıklık:** Evet, getirir. Ağ hataları, servisler arası timeout ve retry, dağıtık veri tutarlılığı (Chroma ↔ uploaded_pdfs ↔ usage.db), çoklu imaj ve CI ile her serviste ayrı model RAM'i gerekir. Bugünkü yükte (tek kullanıcı, tek makine) karşılığı yok.

**Önerilen yol:** Modüler monoliti koru, **mikroservise hazır sınırları** kod içinde netleştir (servis fonksiyonları zaten bu şekilde). Öğrenme amacıyla ileride denenebilecek tek mantıklı ayrım (opsiyonel, Faz 11):
- `chroma` (server mode) + `ingestion-worker`. Yalnızca birden fazla kullanıcı ve uzun yüklemeler gerçek bir sorun olursa, yeterli RAM varsa ve DB'nin bir kopyası üzerinde denenerek. Mevcut `chroma_db_normalization` buna dokunulmadan korunur.

## 6. Sentry entegrasyon noktaları

**Backend (`sentry-sdk[fastapi]`, tek dependency):** `main.py`'nin başında, `SENTRY_DSN` env'de tanımlıysa `sentry_sdk.init(...)` çağrılır; tanımlı değilse hiçbir şey olmaz (CI ve testler etkilenmez).
- `send_default_pii=False`, **`include_local_variables=False`**. Bu kritik: stack frame'lerindeki local'ler prompt, chunk metni ve API key içerir.
- `max_request_body_size="never"` (PDF ve soru gövdesi gönderilmez).
- `before_send` / `before_breadcrumb` scrubber: `x-goog-api-key` / `authorization` header'ları, `question`, `raw_text` alanları ve query string temizlenir.
- **Beklenen ve beklenmeyen hata ayrımı:** 4xx HTTPException'lar gönderilmez (SDK varsayılanı yalnızca 5xx). `PdfUnreadableError` ve `LLMUnavailableError` *beklenen* hatalardır, event olarak gitmez, Prometheus sayacına yazılır. Ancak circuit breaker açıldığında tek bir uyarı event'i gönderilir. Sentry'ye yalnızca `logger.exception` ile loglanan beklenmeyen 500'ler gider.
- **Çift event riski:** `except Exception: logger.exception(...); raise HTTPException(500)` deseni hem logging integration'dan hem de 500 yanıtından event üretebilir. Çözüm: `failed_request_status_codes`'tan 500 çıkarılır, event'ler logging üzerinden gelir.
- Etiketler: `service=rag-backend`, `request_id`, `route` (template, ör. `/documents/{name}`; dosya adı değil), `environment`, `release` (git SHA).
- Kapsanan hatalar: endpoint hataları (otomatik), Gemini hataları (`classify_llm_error` → None olanlar, yani geçersiz key / model adı), Chroma hataları (`get_db`, `similarity_search`), model yükleme hataları (lifespan'da exception → başlangıç hatası olarak yakalanır, init lifespan'dan önce çağrılmalı).
- `traces_sample_rate=0` (tracing'i OTel yapacak; ikisini birden açmak gereksiz).

**Frontend (`@sentry/nextjs`):** Faydalı ama ağır bir paket (build süresini ve bundle'ı büyütür). Faz 9'da, yalnızca client-side hatalar ve `apiService` fetch hataları için. Replay ve tracing kapalı. Alternatif: bağımlılık eklemeden `app/error.tsx` error boundary.

## 7. Structured logging stratejisi

- **Bağımlılıksız:** stdlib `logging` + `JsonFormatter` (`core/log_setup.py`; `logging.py` adı stdlib modülünü gölgeleyebileceği için kullanılmadı). Alanlar: `ts`, `level`, `logger`, `request_id`, `msg`, `exc` (traceback). `service` ve `event` alanları ihtiyaç olunca eklenir.
- `LOG_FORMAT=text|json` ayarı: yerelde okunabilir text kalır (bugünkü format), Docker'da json kullanılır.
- **Request ID:** küçük bir ASGI middleware gelen `X-Request-ID`'yi kabul eder (uzunluk ve karakter doğrulamasıyla) ya da `uuid4` üretir, `contextvars`'a yazar ve yanıta header olarak ekler. Bir `logging.Filter` her kayda `request_id` ekler. Starlette `run_in_threadpool` contextvars'ı thread'e kopyaladığı için sync `/query/` ve `process_pdf` loglarında da ID görünür (bu bir testle doğrulanacak).
- CORS: `expose_headers=["X-Request-ID"]`, `allow_headers`'a `X-Request-ID` eklenir. Frontend hata mesajında ID'yi gösterebilir (opsiyonel).
- uvicorn access/error logger'ları aynı formatter'a bağlanır.
- **İçerik kuralları:** chunk metni, prompt, parola ve API key loglanmaz. Soru metni INFO'da kalır (kullanıcı kararı). Upload'da tam yol yerine dosya adı (Faz 0b). Mevcut `log_timings` satırı ve "Sonuç yok" mesajları zaten temiz; bunlar `event` alanlı kayıtlara dönüşür.
- "Farklı mikroservislerden aynı ID": tek servis olduğu için ID zinciri **Frontend → Backend → (Gemini çağrısında span/log)**. İleride servis eklenirse middleware ve httpx header propagation aynen kullanılır.

## 8. Prometheus + Grafana stratejisi

**Backend (`prometheus-client`, küçük ve bağımlılıksız):** `GET /metrics`. Tek uvicorn worker olduğu için multiprocess moduna gerek yok.

| Metrik | Tür | Etiketler |
|---|---|---|
| `http_requests_total` | Counter | method, route (template), status |
| `http_request_duration_seconds` | Histogram | method, route |
| `rag_stage_duration_seconds` | Histogram | stage = search, rerank, llm |
| `ingestion_duration_seconds` / `ingestion_chunks_total` | Histogram / Counter | result |
| `llm_tokens_total` | Counter | kind = input, thinking, output |
| `llm_errors_total` | Counter | kind = timeout, quota, server, connection, other |
| `rag_no_result_total` | Counter | reason = e5_threshold, rerank_threshold, bilgi_yok, empty |
| `process_resident_memory_bytes`, `process_cpu_seconds_total` | (otomatik) | – (Linux/Docker'da) |

- **Embedding ve Chroma süresini ayırma riski:** bugün "arama" = embedding + Chroma. Ayırmak için `embed_query` + `similarity_search_by_vector...` gerekir; ancak bu yol **farklı skor dönüşümü** kullanabilir ve 0.72 eşiğini bozabilir. İlk aşamada `search` tek metrik olarak kalır; ayrım OTel span'ında (zaman damgasıyla) yapılır ya da eval ile birebir aynı sonuç kanıtlanırsa değişir.
- Etiket kardinalitesi: route template kullanılır, dosya adı / soru / request_id etiket olmaz.
- `/metrics` dışarı açılmaz (compose iç ağı; host portu yalnızca 8000 API için kalır). Starlette'de path'e göre kısıt veya ayrı bir port (prometheus_client `start_http_server(9100)`) değerlendirilecek.
- CPU/RAM için node-exporter veya cAdvisor **eklenmez** (RAM tasarrufu); process metrikleri yeterli.

**Compose (`profiles: [monitoring]`):** `prometheus` (scrape 15 sn, retention 3 gün, mem_limit 256M) + `grafana` (mem_limit 256M, provisioned datasource ve dashboard JSON'u repoda, admin şifresi `.env`'den). Dashboard: p50/p95 API latency, route bazında hata oranı, aşama bazında latency (search / rerank / llm), Gemini hata türleri, token tüketimi / pencere, no_result nedenleri, RSS ve CPU.

## 9. OpenTelemetry stratejisi

- Paketler: `opentelemetry-sdk`, `-exporter-otlp-proto-http`, `-instrumentation-fastapi`, `-instrumentation-httpx` (Gemini çağrısı httpx üzerinden gider). **Opsiyonel** olarak ayrı `requirements-otel.txt` veya `OTEL_ENABLED` ile.
- Manuel span'lar: `rag.query` → `rag.search` (`embed` + `chroma` alt span'ları mümkünse) → `rag.rerank` → `rag.llm` (httpx span'ı altında). `ingest.parse` → `ingest.chunk` → `ingest.embed_batch`.
- Span attribute'ları **yalnızca sayısal ve meta veri:** `k`, `passed_chunks`, `top_rerank_score`, `tokens.*`, `finish_reason`, `error.kind`. Prompt, soru, chunk ve dosya içeriği yok. httpx instrumentation'da header ve body capture kapalı; URL'de key yok (google-genai key'i header'da gönderiyor, doğrulanacak).
- `trace_id` log kayıtlarına da eklenir (log ↔ trace ilişkisi). Request ID ile trace ID aynı yapılabilir veya ikisi birlikte loglanabilir.
- Frontend: browser OTel SDK ağır, **eklenmez**. Fetch'te `X-Request-ID` (ve istenirse `traceparent`) header'ı üretmek yeterli.
- Backend: `jaeger` all-in-one (`profiles: [tracing]`, bellek içi depolama, `MEMORY_MAX_TRACES` ve mem_limit 256M).
- Cevaplanacak sorular (retrieval mi, LLM mi; hangi aşama hata verdi) span süreleriyle doğrudan görülür. Not: bugünkü `log_timings` zaten bunun kaba bir halini veriyor (Docker 28 sn ve native 9 sn farkı bu şekilde netleşir).

## 10. Timeout, retry, circuit breaker

| Bileşen | Bugün | Öneri |
|---|---|---|
| Gemini timeout + retry | 20 sn × 2 deneme (kütüphane, backoff dahil) | Aynen kalır. **Kendi retry'ımızı eklemeyiz** (çift retry sorunu). Toplam bütçe ~40 sn; frontend'e AbortController (~60 sn) eklenebilir. |
| Gemini circuit breaker | Yok | In-process, ~40 satır, dependency yok: ardışık N=5 geçici hatada 30 sn açık kalır. Bu sürede Gemini çağrılmaz, hemen 503 döner; ardından half-open'da tek deneme. Quota (429) hatasında daha uzun açık kalabilir. Durum, metrik ve readiness'e yansır. |
| Chroma | Embedded; ağ yok | Retry anlamlı değil (hatalar disk ve SQLite lock kaynaklı). Readiness'te `collection.count()` kontrolü yapılır. Chroma hatası 503 + Sentry olur. |
| Ingestion idempotency | Deterministik ID + kilit + yarım yazmada geri alma (yapıldı) | Aynen kalır. İstemci retry'ı ID'ler deterministik olduğu için duplicate üretmez. Upload endpoint'inde otomatik retry yok, olmamalı. Geri alma hatası Faz 3'te Sentry'ye gider. |
| Health | `GET /` sabit cevap veriyor | `GET /health/live` (process ayakta) · `GET /health/ready` (modeller yüklü + Chroma count + Gemini breaker kapalı/half-open; Gemini'ye istek **atılmaz**, token harcanmaz). `/` korunur. |
| Graceful shutdown | `--reload` ile belirsiz | `--reload` kaldırılır (dev override'a taşınır); `--timeout-graceful-shutdown 30`, compose'da `stop_grace_period: 40s`. Uzun upload bitmeden kesilirse geri alma devreye girer. |

## 11. Docker Compose değişiklikleri

- `docker-compose.yml`, production'a yakın taban: `--reload` yok, `.:/app` bind mount yok. Veri mount'ları korunur: `./uploaded_pdfs`, `./chroma_db_normalization` (açık mount, **mevcut DB olduğu gibi kullanılır**), `./usage.db` (veya bir data klasörü), `hf_cache` volume'u.
- `docker-compose.override.yml` (dev): bugünkü `.:/app` + `--reload`. **`docker compose up` bugünkü gibi davranmaya devam eder** (override otomatik yüklenir), yani mevcut kullanım bozulmaz.
- `healthcheck`: `python -c "urllib.request.urlopen('http://localhost:8000/health/ready')"` (slim imajda curl yok), `start_period: 120s` (model yükleme süresi).
- `profiles`: `monitoring` (prometheus, grafana), `tracing` (jaeger). Varsayılan açılışta ek RAM harcanmaz.
- Opsiyonel `frontend` servisi (Next.js standalone build, ~150 MB RAM). `NEXT_PUBLIC_API_URL` build-time olduğu için build arg gerekir.
- Ayrıca: Dockerfile'da non-root kullanıcı (faydalı), `version:` alanının kaldırılması (deprecated), `container_name` sabit kalır.
- Kaynak limitleri (2 CPU / 3 GB) korunur. Sabit `dns` ayarı Gemini bağlantı sorunu için eklenmişti; kaldırılmadan önce gerekip gerekmediği denenecek.
- Port zaten yalnızca `127.0.0.1`'e açık.

## 12. Güvenlik ve veri gizliliği

- Soru metni INFO logda kalır (kullanıcı kararı); Sentry'ye gidip gitmeyeceğine Faz 3'te karar verilir. Upload loglarında tam yol yerine ad (Faz 0b).
- Sentry: `include_local_variables=False`, `send_default_pii=False`, body gönderilmez, scrubber + bunun için **unit test** yazılır. Server tarafında da "Data Scrubbing" açık tutulur.
- Metrik ve trace etiketlerinde içerik ve dosya adı yok; dosya adının kendisi de hassas olabilir.
- `/metrics`, Prometheus, Grafana ve Jaeger portları host'a yalnızca `127.0.0.1` ile bind edilir. Grafana admin şifresi `.env`'de tutulur, `.env.example`'da boş bırakılır.
- `SENTRY_DSN` secret sayılır (gizli değil ama kötüye kullanılabilir); `.env`'de durur, image'a girmez (`.dockerignore` zaten `.env`'i dışlıyor).
- Gelen `X-Request-ID` doğrulanır (≤64 karakter, `[A-Za-z0-9-]`); doğrulanmazsa log injection'a yol açar.
- `/health/*` ve `/metrics` auth'suz olacaksa yalnızca durum bilgisi vermeli, doküman adı veya içerik vermemeli.

## 13. RAM ve performans maliyeti (tahmini)

| Bileşen | RAM | Not |
|---|---|---|
| Mevcut backend | ~1.8–2.3 GB | e5-base ~1.1 GB, mMiniLM ~0.45 GB, torch ve Chroma |
| + JSON log, request ID | ~0 | |
| + sentry-sdk | ~15–25 MB | Ağ çağrısı arka planda; istek başına ~0 ms |
| + prometheus-client | ~5–10 MB | İstek başına mikrosaniyeler |
| + OpenTelemetry SDK | ~30–50 MB | Batch exporter; istek başına <1 ms |
| Prometheus (profil) | 100–200 MB | retention 3 gün |
| Grafana (profil) | 100–150 MB | |
| Jaeger (profil) | 50–200 MB | Bellek içi, trace limitli |
| Frontend container (opsiyonel) | ~100–150 MB | |
| **Mikroservis ayrımı (karşılaştırma için)** | **+1.3–2 GB** | 2. model kopyası + Chroma server |

## 14. Test stratejisi

Testler `unittest` ile yazılıyor (`python -m unittest discover tests`; README'de pytest denmiş olabilir, CI unittest kullanıyor). Yeni testler aynı yapıda olacak; Gemini, Chroma ve modeller mevcut testlerdeki gibi sahte (mock) olacak.

- `test_logging.py`: JSON formatter alanları; `request_id` header'ının yanıtta dönmesi; gelen geçersiz ID'nin reddedilmesi; threadpool'daki logda aynı ID'nin görünmesi; parola ve API key'in hiçbir log kaydında geçmemesi.
- `test_sentry_scrub.py`: `before_send`'in header, body ve `question` alanlarını temizlemesi; DSN yokken init yapılmaması; 4xx ve beklenen hataların gönderilmemesi.
- `test_metrics.py`: `/metrics` 200 dönüyor; bir query sonrası sayaçlar ve aşama histogramları artıyor; route etiketi template.
- `test_health.py`: live her zaman 200; ready, Chroma hatasında veya breaker açıkken 503.
- `test_circuit_breaker.py`: closed → open → half-open → closed geçişleri; açıkken Gemini mock'u çağrılmıyor ve 503 dönüyor.
- Değişmeyen davranış: mevcut testlerin hepsi geçmeli. Retrieval kodu değişirse `eval/evaluate_evidence.py` ve `evaluate_negative.py` ile sonuçların `eval/results/` ile **birebir** aynı olduğu doğrulanır.
- Docker: `docker compose build && up`, healthcheck'in `healthy` olması, `/query/` ve `/upload-pdf/` için manuel smoke test. `--profile monitoring` ile Grafana'da verinin göründüğü kontrol edilir.
- CI: yeni bağımlılıklar `requirements.txt`'e eklenir; OTel opsiyonelse ayrı tutulur ve CI'da kurulmaz (testler OTel yokken de geçmeli).

## 15. Bulutta çalıştırma (deployment)

**Platform seçimi**

| Seçenek | Karar | Neden |
|---|---|---|
| **VPS + Docker Compose** (Hetzner, DigitalOcean, Lightsail vb.) | ✅ Önerilen | Mevcut compose dosyası neredeyse aynen kullanılır. Disk kalıcı olduğu için Chroma, `uploaded_pdfs` ve `usage.db` bind mount'ları korunur. Önerilen boyut **4 vCPU / 8 GB RAM**. Kesin fiyat ve bölge, seçim yapılırken kontrol edilecek. |
| Sunucusuz container (Cloud Run, Render, Railway) | ❌ | Container diski kalıcı değil (yüklenen PDF'ler ve chunk'lar kaybolur). Her soğuk açılışta ~1.6 GB model yükleniyor. |
| Kubernetes | ❌ | Tek uygulama için gereksiz karmaşıklık ve maliyet. |

**Bulutun önceki kararlara etkisi**
- Mikroservis: değişmiyor. RAM gevşiyor ama ikinci model kopyası (+1.5 GB) hâlâ bir kazanç sağlamıyor.
- Monitoring: 8 GB'lık makinede Prometheus ve Grafana sürekli açık tutulabilir (profil yerine varsayılan servis). Jaeger yine opsiyonel kalır.
- Docker'daki `cpus: 2` / `memory: 3000M` sınırları makine boyutuna göre yeniden ayarlanır (`OMP_NUM_THREADS` ile birlikte). Yerelde Docker'da yaşanan yavaşlığın (notes.md GUNCEL 9) kaynağı §17'de ölçülecek; Windows'a özgü kısmı (dosya köprüsü, WSL2) Linux sunucuda yaşanmaz.
- Sentry SaaS ve Grafana zaten buluttan erişilebilir; self-hosted Sentry için yine 4 GB+ RAM gerektiğinden önerilmiyor.

**İnternete açılınca zorunlu hale gelenler (Faz B)**
- Auth ve auth'lu `/static-pdfs/{name}` yapıldı; internete açıkken `COOKIE_SECURE=true` olmalı (HTTPS).
- **HTTPS reverse proxy:** Caddy (otomatik Let's Encrypt, küçük config, ~20–40 MB). Dışarıya yalnızca 80 ve 443 açık olur; backend'in 8000 portu ve `/metrics`, Grafana, Jaeger host'a açılmaz (Grafana gerekiyorsa SSH tüneli veya auth'lu bir alt yol üzerinden).
- **Rate limit:** IP bazlı, Caddy'de veya backend'de. Login için `LoginGuard` zaten var (5 hatalı deneme → 15 dk kilit); diğer endpoint'ler için gerekli.
- **CORS:** `CORS_ORIGINS` gerçek domain olur; `ALLOW_DELETE` auth ile birlikte yeniden değerlendirilir.
- **Firewall:** sağlayıcı firewall'unda yalnızca 22 (tercihen yalnızca kendi IP'nizden), 80 ve 443 açık; SSH'ta key ile giriş, parola kapalı.
- **Proxy arkasında doğru istemci IP'si:** uvicorn `--proxy-headers --forwarded-allow-ips` ayarı. Rate limit ve loglar için gerekli.

**Deploy süreci**
- İmaj GitHub Actions'ta derlenir → GHCR'a push edilir → sunucuda `docker compose pull && up -d` çalışır (ilk aşamada manuel ya da SSH ile, ileride workflow ile).
- Frontend: Next.js standalone container'ı (bu Faz 10'u öne çeker) veya statik host (Vercel vb.). `NEXT_PUBLIC_API_URL` build sırasında gerçek domain ile verilir.
- Secret'lar: sunucuda `.env` (izinleri 600), repoda yok. CI'da gerekiyorsa GitHub Secrets.
- İlk veri taşıma: `chroma_db_normalization/`, `uploaded_pdfs/`, `usage.db` sunucuya `rsync`/`scp` ile **kopyalanır** (yerel DB silinmez, yeniden kurulmaz). Modeller ilk açılışta `HF_HUB_OFFLINE=0` ile bir kez indirilir.
- Yedekleme: üç veri klasörü için günlük snapshot veya `tar` ile yedek; geri yükleme bir kez denenir.

**Veri gizliliği**
- Kurumsal PDF'ler yurt dışındaki bir sunucuda saklanacak. KVKK açısından sağlayıcı ve bölge seçimi bilinçli bir karar olmalı (AB bölgesi tercih edilebilir). Gemini'ye zaten chunk gönderiliyor; bu durum değişmiyor ama dokümante edilmeli.

**Test**
- Sunucuda `curl https://<domain>/health/ready`, frontend'den soru ve upload smoke testi.
- Oturumsuz istekte 401, rate limit aşımında 429 dönüyor mu (frontend 429'u token sınırı sanıyor; ayrı bir kod veya mesaj gerekebilir, `services/api.ts`).
- Port taraması: dışarıdan yalnızca 22, 80 ve 443 açık mı.
- Restart sonrası verinin kaybolmadığı kontrol edilir.

## 16. Gemini kotası, az trafik ve sahte LLM modu (Faz L)

**Sorun:** Ücretsiz Gemini API'si günde ~20 istekle sınırlı ve uygulamayı tek kişi kullanıyor. Ölçüm için ne yeterli istek ne de yeterli trafik var.

**Hangi fazlar etkilenmiyor:**
- Faz 1 (log, request ID), Faz 2 (health; `/health/ready` Gemini'ye istek atmaz), Faz 3 (Sentry; hata bilerek tetiklenir) tek kullanıcıyla tam değer verir. Bunlar istatistik için değil, tek tek olayları incelemek için.
- Faz 5 (circuit breaker) unit testte mock ile test edilir.
- Faz R'nin retrieval eval'i (Hit@k, MRR) Gemini çağırmaz; yalnızca Deneme C Gemini ister.
- Gemini'nin 429 (kota) hatası da metrik ve circuit breaker için gerçek bir veri.

**Etkilenenler:** Faz 4 (metrikler) ve Faz 6 (Grafana) trafik ister. Çözüm: sahte LLM modu + yük testi.

**Sahte LLM modu:** Soru akışında (arama → eşik → reranker → prompt → **Gemini** → cevap ayıklama → token kaydı) yalnızca Gemini adımı değişir; geri kalanı gerçek çalışır (gerçek e5, reranker, DB, log, metrik).

| Davranış | Açıklama |
|---|---|
| Cevap | `[SAHTE CEVAP]` önekli; reranker'ın seçtiği ilk parçadan alıntı ve gerçek kaynaklar (PDF sayfa atlama da test edilir) |
| Gecikme | `FAKE_LLM_DELAY_SECONDS` (ör. 3); beklemezse yük testi gerçekçi olmaz |
| Hata | `FAKE_LLM_ERROR_RATE` (ör. 0.1): timeout / kota hatası; uygulama gerçek Gemini hatası gibi işler (503/504, log, metrik, circuit breaker) |
| Token | Metin uzunluğundan tahmini sayı; metrikler dolar ama `usage.db`'deki gerçek günlük sınırı tüketmez (yazılmaz veya ayrı işaretlenir, planda netleşecek) |

**Güvenlik:** Varsayılan `LLM_MODE=gemini`. Sahte modda açılışta WARNING; `/health/ready` modu gösterir; cevaplar `[SAHTE CEVAP]` etiketli. Sahte modda Gemini API key kontrolü atlanır (küçük bir istisna kuralı).

**Ölçer:** arama ve reranker süresi, eşzamanlı isteklerde davranış, RAM/CPU tıkanması, upload sırasında soruların yavaşlaması, log/metrik/Grafana/circuit breaker.
**Ölçmez:** Gemini'nin gerçek hızı, cevap kalitesi (Deneme C için gerçek Gemini gerekir), Gemini'nin gerçek hata davranışı.

**Testlerdeki mock'tan farkı:** mock yalnızca test kodunda yaşar (model ve DB yok); sahte mod çalışan gerçek uygulamanın içindedir (Docker'da açılır, tarayıcıdan kullanılır).

**Yük testi scripti:** golden (80) + negatif (55) set sorularını ayarlanabilir eşzamanlılıkla (ör. 1, 5, 10) gönderir; süre ve durum kodlarını özetler. Yeni bağımlılık gerektirmez (stdlib veya mevcut `httpx`). Production hâliyle (Faz 2 sonrası, `--reload` kapalı) çalıştırılmalı.

**Maliyet:** ~50–80 satır + test; yeni bağımlılık ve ek RAM yok; `LLM_MODE` yazılmazsa davranış değişmez.

**Alternatif / ek:** Gemini ücretli katman (harcama limitiyle) veya daha yüksek ücretsiz kotası olan bir model. Güncel kota ve fiyatlar Google AI Studio'dan kontrol edilecek.

## 17. Docker yavaşlığı (Faz 2'nin parçası)

**Durum:** notes.md GUNCEL 9'da aynı soru Docker'da ~28 sn, native ~9 sn sürüyordu. Sonradan `OMP_NUM_THREADS=2` eklendi; etkisi yeniden ölçülmedi.

**Olası sebepler (olasılık sırasıyla, ölçülmedi):**
1. **Windows klasör köprüsü (`.:/app`):** Docker Desktop container'ları WSL2 sanal makinesinde çalıştırıyor; Windows klasörüne erişim yavaş. Chroma (SQLite + HNSW) çok sayıda küçük okuma yapıyor.
2. **`--reload` dosya izlemesi:** köprü üzerinden tüm proje klasörünü (venv, node_modules, DB) tarıyor, 2 çekirdeklik kotadan CPU yiyor.
3. **CPU kotası:** native 4 çekirdek, Docker 2 çekirdek; e5 ve reranker CPU yoğun.
4. **WSL2'nin kendi yükü:** genelde en küçük pay.

**Deney (DB'nin kopyasıyla; asıl DB'ye dokunulmaz):**

| Deneme | Değişen | Ne öğreniriz |
|---|---|---|
| A | Bugünkü Docker | Referans |
| B | `--reload` kapalı | Dosya izlemenin payı |
| C | B + DB kopyası Docker named volume'unda | Windows köprüsünün payı |
| D | C + 3 çekirdek | CPU kotasının payı |
| Native | Docker'sız | Hedef |

Her denemede aynı 5 soru; yalnızca `log_timings`'teki **arama + reranker** süresine bakılır (Gemini süresi her yerde aynı, kota harcamamak için sahte mod da kullanılabilir). İlk sorgu (ısınma) ayrı tutulur.

**Faz 2'ye etkisi:** prod hâlinde `--reload` yok (sebep 2 gider) ve kod imajın içinde (sebep 1'in bir kısmı gider). Deney C anlamlı fark gösterirse DB named volume'a taşınır; bu durumda yedekleme ve sunucuya taşıma adımları (§15) buna göre güncellenir. Linux sunucuda sebep 1 ve 4 yaşanmaz.

---

## Yol haritası (öncelik sırasıyla)

Etiketler: 🔴 Production için gerekli · 🟡 Faydalı iyileştirme · ⚪ İleriye dönük / opsiyonel

| Faz | İş | Sınıf | Değişecek dosyalar | Risk | Ek RAM |
|---|---|---|---|---|---|
| 0b | Upload logunda tam yol yerine dosya adı ("kalıcı olarak kaydedildi" satırı) | 🟡 | `main.py` | Yok | 0 |
| 2 | Önce/sonra performans ölçümü (§17) + health/readiness endpoint'leri + Docker prod/dev ayrımı + healthcheck + graceful shutdown + non-root kullanıcı, `version` alanı, sabit `dns` (notes.md #25) | 🔴 | `main.py`, `Dockerfile`, `docker-compose.yml`, yeni `docker-compose.override.yml`, `docs/` | Bugünkü `docker compose up` akışının bozulması (override bunu önler); chroma mount yolunun doğru olması; non-root kullanıcının DB ve `uploaded_pdfs`'e yazma izni; `usage.db` tek dosya mount'u (dosya yoksa Docker klasör oluşturur) | 0 |
| L | Sahte LLM modu (`LLM_MODE=fake`) + yük testi scripti (§16) | 🟡 | `core/settings.py`, `core/config.py` veya yeni `services/fake_llm.py`, `services/query.py`, yeni `scripts/load_test.py`, `.env.example`, `docs/`, test | Production'da yanlışlıkla açık kalması (varsayılan `gemini` + WARNING + etiket); sahte token'ların gerçek sınırı tüketmesi | 0 |
| B | **Bulut hazırlığı:** Caddy (HTTPS), rate limit, firewall, proxy header'ları, prod CORS (§15) | 🔴 (internete açılmadan önce) | yeni `deploy/Caddyfile`, `docker-compose.yml` (veya `docker-compose.prod.yml`), `main.py`, `services/api.ts`, `docs/`, test | Rate limit 429'unun token 429'uyla karışması | ~30 MB (Caddy) |
| B2 | **Deploy:** GHCR'a imaj push eden workflow, sunucu kurulumu, veri kopyalama, yedekleme | 🔴 (bulut için) | yeni `.github/workflows/deploy.yml` (veya yalnızca build+push), `docs/deployment.md` | Secret yönetimi; ilk veri taşıma | – |
| 3 | Sentry (backend) + scrubber + beklenen/beklenmeyen hata ayrımı | 🔴 | yeni `core/observability.py`, `main.py`, `services/query.py` (beklenen hatalar), `core/settings.py`, `requirements.txt`, test | Çift event; local variable sızıntısı (kapatılacak) | ~20 MB |
| 4 | Prometheus metrikleri (`/metrics`) | 🔴 | `core/observability.py`, `main.py`, `services/query.py`, `services/ingestion.py`, `requirements.txt`, test | Kardinalite; search ayrımının skorları bozması (ilk aşamada ayrılmayacak) | ~10 MB |
| 5 | Gemini circuit breaker + readiness'e bağlanması | 🟡 | yeni `services/circuit_breaker.py`, `services/query.py`, `main.py`, test | Yanlış ayarlanırsa gereksiz 503; eşikler `.env`'den | 0 |
| 6 | Prometheus + Grafana (yerelde profil, bulutta varsayılan) + provisioned dashboard | 🟡 | `docker-compose.yml`, yeni `monitoring/` (prometheus.yml, grafana provisioning, dashboard JSON) | RAM; Grafana'nın dışarı açılmaması | 200–350 MB |
| 7 | OpenTelemetry (manuel span + FastAPI/httpx) + Jaeger profili | 🟡/⚪ | `core/observability.py`, `services/query.py`, `services/ingestion.py`, `requirements-otel.txt`, compose | Prompt'un attribute'a sızması (kural + test); dependency sayısı | 30–50 MB + Jaeger (opt-in) |
| 8 | Frontend: `X-Request-ID` gönderme ve hata mesajında gösterme, fetch timeout (AbortController), `error.tsx`, beklenen hatalarda `console.error` yerine `warn`, cevap `console.log`'unun kaldırılması; backend: PDF cevabında `Cache-Control: private, no-cache` | 🟡 (PDF önbelleği Faz B'den önce 🔴) | `rag-frontend/services/api.ts`, `components/Workspace.tsx`, `app/error.tsx`, `main.py`, test | PDF açılışlarının yavaşlaması (tarayıcı `304` yerine her seferinde tam indirirse; Network sekmesinde kontrol) | 0 |
| 9 | Sentry (frontend, `@sentry/nextjs`) | ⚪ | `rag-frontend/` config dosyaları | Build ağırlaşır | 0 (backend) |
| 10 | Frontend container'ı (standalone) compose'a; bulutta frontend de sunucuda olacaksa Faz B2'ye taşınır | 🟡 (bulut) / ⚪ | yeni `rag-frontend/Dockerfile`, compose | Build-time env | ~150 MB |
| R | Retrieval kalitesi (notes.md #13): q23, hibrit arama denemesi, Deneme C (Gemini kotası uygunken) | 🟡 | `services/query.py`, `eval/`, yeni deneme klasörü | Eval sonuçları golden + negatif setle ölçülmeden production'a girmez; mevcut DB'ye dokunulmaz (deneme kopyada) | hibrit aramaya göre |
| S | Streaming cevap (notes.md #43) | 🟡 | `main.py`, `services/query.py`, `rag-frontend/services/api.ts`, `ChatArea.tsx`, test | Token sayımı ve hata yönetiminin stream içinde yapılması; mevcut `/query` API'si bozulmamalı (ayrı endpoint) | 0 |
| 11 | Chroma server mode + ayrı ingestion worker (öğrenme denemesi, DB **kopyasında**) | ⚪ | çok sayıda | Yüksek: RAM, tutarlılık, yeniden mimari | +1.3–2 GB |

**Her fazda süreç:** uygulamadan önce kısa plan ve onay → uygulama → `ruff check .` + `python -m unittest discover tests` → (Docker fazlarında) build + healthcheck + smoke test → değişen dosyalar, test çıktısı ve kalan sorunlarla rapor.
