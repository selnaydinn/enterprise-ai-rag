# Enterprise AI — Production Hazırlığı: Mimari Analiz

> Tarih: 2026-11-11 · Branch: `fatures/retrieval-quality` (2db109c) · Durum: **yalnızca analiz, kod değişikliği yok**
> Açık kalan eski maddeler `notes.md`'de; bu dosya yeni production yol haritası.

## Özet (TL;DR)

- **Mikroservise bölmeyi önermiyorum.** Proje temiz katmanlı bir *modüler monolit*. Ingestion ve query aynı embedding modelini (e5-base, ~1.1 GB RAM) ve aynı gömülü (embedded) Chroma'yı kullanıyor. Bölmek, modeli iki kez yüklemek (+1.1–1.6 GB) ve Chroma'yı ayrı bir sunucu container'ına taşımak demek. 3 GB'lık container sınırında bu kaldırılamaz, karşılığında da gerçek bir kazanç yok.
- **Asıl değer gözlemlenebilirlikte (observability):** request ID'li JSON loglar → Sentry (backend) → `/metrics` (Prometheus) → health/readiness → Gemini için circuit breaker → OpenTelemetry (opsiyonel).
- **İncelemede bulunan 3 gerçek sorun (önce bunlar düzeltilmeli):**
  1. Upload yarıda kalırsa (ör. 3. batch'te hata) ilk batch'lerin chunk'ları DB'de kalıyor, dosya diskten siliniyor. Tekrar yüklemede `is_file_already_processed` true dönüyor ve **409 veriyor: doküman yarım indeksli kalıyor ve düzeltilemiyor.**
  2. `search_in_db` soru metnini INFO seviyesinde logluyor (`services/query.py`). Sentry'ye breadcrumb olarak da gider.
  3. Docker imajı `--reload` ve `.:/app` bind mount ile çalışıyor; bu ayar geliştirme için, production için değil. Graceful shutdown ve healthcheck de yok.
- **Ek RAM (önerilen yol):** backend'e +~50–80 MB. Monitoring stack'i (Prometheus + Grafana + Jaeger) ancak `profiles` ile istenince açılıyor ve ~350–500 MB ekliyor.
- **Bulut (2026-11-11 eki):** Hedef, container'ları bir **VPS'te Docker Compose** ile çalıştırmak. Bu RAM sınırını gevşetiyor ama mikroservis kararını değiştirmiyor. İnternete açılmadan önce yeni **Faz B (bulut hazırlığı)** tamamlanmalı: HTTPS reverse proxy, rate limit, `/static-pdfs` koruması, kapalı portlar. Auth `notes.md` #4 kapsamında ayrıca kapatılıyor ve Faz B'nin ön koşulu. Ayrıntılar §15'te.

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

**Upload:** `POST /upload-pdf/` → ad, boyut ve `%PDF-` kontrolü → md5 hash → `UPLOAD_LOCK` → duplikasyon kontrolü (409) → diske yazma → `process_pdf` threadpool'da (PyPDF → normalize → split → 32'lik batch'lerle `add_documents`; ID = `h_{hash}_p{page}_c{idx}`, deterministik).

**Diğer:** `GET /documents/`, `/usage/`, `/features/`, `DELETE /documents/{name}` (aynı kilitle), `/static-pdfs` (StaticFiles), `GET /` (sabit cevap; bağımlılıkları kontrol etmiyor).

**Hata modeli (iyi durumda):** kullanıcı hataları 4xx; Gemini'nin geçici hataları `LLMUnavailableError` → 503/504; beklenmeyen hatalar genel 500 dönüyor, stack trace kullanıcıya gitmiyor. Başlangıçta key, DB ve modeller yüklenip warm-up yapılıyor (fail-fast).

## 2. Monolit mi, modüler mi?

**Modüler monolit.** Katmanlar net: HTTP (main) / config / domain servisleri / persistence. Servisler HTTP'den habersiz, test edilebilir ve exception tabanlı. Tek deployment birimi, tek process ve paylaşılan bellek içi modeller var. `main.py` 277 satır, henüz router'lara bölmeyi gerektirecek kadar büyük değil.

## 3–5. Mikroservis değerlendirmesi

| Aday servis | Ayrılmalı mı? | Gerekçe |
|---|---|---|
| API Gateway | **Hayır** | Tek backend var; yönlendirilecek bir şey yok. FastAPI zaten validation yapıyor. Auth gelince de aynı uygulamada middleware/dependency olur. |
| Ingestion | **Şimdilik hayır** | e5 modeli gerekli (+1.1 GB). Embedded Chroma'ya iki process'ten yazmak güvenli değil (SQLite + HNSW dosyaları) ve Chroma server container'ı gerektirir (+200–400 MB). Tek kazanç, uzun bir upload'ın query'leri CPU'da yavaşlatmaması; bu da `cpus` kotasıyla sınırlı. |
| Query (retrieval + rerank) | **Hayır** | Ingestion ile aynı modeli ve DB'yi paylaşıyor; ayrılırsa veri sahipliği belirsizleşir. |
| Generation | **Hayır** | Gemini'ye yapılan ince bir HTTP çağrısı. Ayrı servis olursa bir ağ adımı, bir serileştirme ve bir hata noktası daha ekler. Failure isolation zaten `LLMUnavailableError` ve circuit breaker ile sağlanabilir. |
| Token usage | **Hayır** | Query yolunda senkron ve 2 sorguluk bir işlem. Ayrılırsa her soruya 2 ağ çağrısı eklenir ve tutarlılık sorunu çıkar (Gemini cevap verdi, usage servisi kapalıysa ne olacak?). |

**Gereksiz karmaşıklık:** Evet, getirir. Ağ hataları, servisler arası timeout ve retry, dağıtık veri tutarlılığı (Chroma ↔ uploaded_pdfs ↔ usage.db), çoklu imaj ve CI ile her serviste ayrı model RAM'i gerekir. Bugünkü yükte (tek kullanıcı, tek makine) karşılığı yok.

**Önerilen yol:** Modüler monoliti koru, **mikroservise hazır sınırları** kod içinde netleştir (servis fonksiyonları zaten bu şekilde). Öğrenme amacıyla ileride denenebilecek tek mantıklı ayrım (opsiyonel, Faz 7):
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

**Frontend (`@sentry/nextjs`):** Faydalı ama ağır bir paket (build süresini ve bundle'ı büyütür). Faz 2b'de, yalnızca client-side hatalar ve `apiService` fetch hataları için. Replay ve tracing kapalı. Alternatif: bağımlılık eklemeden `app/error.tsx` error boundary.

## 7. Structured logging stratejisi

- **Bağımlılıksız:** stdlib `logging` + ~30 satırlık bir `JsonFormatter` (`core/logging.py`). Alanlar: `ts`, `level`, `service`, `logger`, `request_id`, `msg`, `event` (opsiyonel işlem adı), `exc_type` + `exc` (traceback).
- `LOG_FORMAT=text|json` ayarı: yerelde okunabilir text kalır (bugünkü format), Docker'da json kullanılır.
- **Request ID:** küçük bir ASGI middleware gelen `X-Request-ID`'yi kabul eder (uzunluk ve karakter doğrulamasıyla) ya da `uuid4` üretir, `contextvars`'a yazar ve yanıta header olarak ekler. Bir `logging.Filter` her kayda `request_id` ekler. Starlette `run_in_threadpool` contextvars'ı thread'e kopyaladığı için sync `/query/` ve `process_pdf` loglarında da ID görünür (bu bir testle doğrulanacak).
- CORS: `expose_headers=["X-Request-ID"]`, `allow_headers`'a `X-Request-ID` eklenir. Frontend hata mesajında ID'yi gösterebilir (opsiyonel).
- uvicorn access/error logger'ları aynı formatter'a bağlanır.
- **İçerik kuralları:** soru metni, chunk metni, prompt ve API key loglanmaz. Soru yerine `question_len` ve gerekirse `question_sha8`. Upload'da tam yol yerine dosya adı. Mevcut `log_timings` satırı ve "Sonuç yok" mesajları zaten temiz; bunlar `event` alanlı kayıtlara dönüşür.
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
| Ingestion idempotency | Deterministik ID + kilit | **Yarım yazmada geri alma eklenmeli:** `process_pdf` hata verirse o `pdf_hash`'e ait chunk'lar silinir (`db.delete(where={"pdf_hash": ...})`). İstemci retry'ı ID'ler deterministik olduğu için duplicate üretmez. Upload endpoint'inde otomatik retry yok, olmamalı. |
| Health | `GET /` sabit cevap veriyor | `GET /health/live` (process ayakta) · `GET /health/ready` (modeller yüklü + Chroma count + Gemini breaker kapalı/half-open; Gemini'ye istek **atılmaz**, token harcanmaz). `/` korunur. |
| Graceful shutdown | `--reload` ile belirsiz | `--reload` kaldırılır (dev override'a taşınır); `--timeout-graceful-shutdown 30`, compose'da `stop_grace_period: 40s`. Uzun upload bitmeden kesilirse geri alma devreye girer. |
| Upload sırasında Chroma / embedding hatası | 500 + dosya silinir | Aynı davranış + chunk geri alma + Sentry. |

## 11. Docker Compose değişiklikleri

- `docker-compose.yml`, production'a yakın taban: `--reload` yok, `.:/app` bind mount yok. Veri mount'ları korunur: `./uploaded_pdfs`, `./chroma_db_normalization` (açık mount, **mevcut DB olduğu gibi kullanılır**), `./usage.db` (veya bir data klasörü), `hf_cache` volume'u.
- `docker-compose.override.yml` (dev): bugünkü `.:/app` + `--reload`. **`docker compose up` bugünkü gibi davranmaya devam eder** (override otomatik yüklenir), yani mevcut kullanım bozulmaz.
- `healthcheck`: `python -c "urllib.request.urlopen('http://localhost:8000/health/ready')"` (slim imajda curl yok), `start_period: 120s` (model yükleme süresi).
- `profiles`: `monitoring` (prometheus, grafana), `tracing` (jaeger). Varsayılan açılışta ek RAM harcanmaz.
- Opsiyonel `frontend` servisi (Next.js standalone build, ~150 MB RAM). `NEXT_PUBLIC_API_URL` build-time olduğu için build arg gerekir.
- Ayrıca: Dockerfile'da non-root kullanıcı (faydalı), `version:` alanının kaldırılması (deprecated), `container_name` sabit kalır.
- DNS ayarı ve kaynak limitleri (2 CPU / 3 GB) korunur.

## 12. Güvenlik ve veri gizliliği

- Soru metni loglardan çıkarılır (madde: `search_in_db`). Upload loglarında tam yol yerine ad kullanılır.
- Sentry: `include_local_variables=False`, `send_default_pii=False`, body gönderilmez, scrubber + bunun için **unit test** yazılır. Server tarafında da "Data Scrubbing" açık tutulur.
- Metrik ve trace etiketlerinde içerik ve dosya adı yok; dosya adının kendisi de hassas olabilir.
- `/metrics`, Prometheus, Grafana ve Jaeger portları host'a yalnızca `127.0.0.1` ile bind edilir. Grafana admin şifresi `.env`'de tutulur, `.env.example`'da boş bırakılır.
- `SENTRY_DSN` secret sayılır (gizli değil ama kötüye kullanılabilir); `.env`'de durur, image'a girmez (`.dockerignore` zaten `.env`'i dışlıyor).
- Gelen `X-Request-ID` doğrulanır (≤64 karakter, `[A-Za-z0-9-]`); doğrulanmazsa log injection'a yol açar.
- Mevcut açık konu (bu kapsamın dışında): kimlik doğrulama yok (`notes.md` #4) ve `/static-pdfs` herkese açık. Gözlemlenebilirlik araçları bu açığı büyütmemeli.

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

- `test_logging.py`: JSON formatter alanları; `request_id` header'ının yanıtta dönmesi; gelen geçersiz ID'nin reddedilmesi; threadpool'daki logda aynı ID'nin görünmesi; **soru metninin hiçbir log kaydında geçmemesi**.
- `test_sentry_scrub.py`: `before_send`'in header, body ve `question` alanlarını temizlemesi; DSN yokken init yapılmaması; 4xx ve beklenen hataların gönderilmemesi.
- `test_metrics.py`: `/metrics` 200 dönüyor; bir query sonrası sayaçlar ve aşama histogramları artıyor; route etiketi template.
- `test_health.py`: live her zaman 200; ready, Chroma hatasında veya breaker açıkken 503.
- `test_circuit_breaker.py`: closed → open → half-open → closed geçişleri; açıkken Gemini mock'u çağrılmıyor ve 503 dönüyor.
- `test_upload.py` ek: 2. batch'te hata → DB'de o hash'e ait chunk kalmıyor; ardından aynı dosya tekrar yüklenebiliyor (bugün 409 veriyor, regresyon testi).
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
- Docker'daki `cpus: 2` / `memory: 3000M` sınırları makine boyutuna göre yeniden ayarlanır (`OMP_NUM_THREADS` ile birlikte). Docker'da yaşanan 28 sn'lik yanıt süresi (notes.md GUNCEL 9) büyük ölçüde CPU kotasından kaynaklanıyordu; bulutta Faz 4'teki metriklerle ölçülecek.
- Sentry SaaS ve Grafana zaten buluttan erişilebilir; self-hosted Sentry için yine 4 GB+ RAM gerektiğinden önerilmiyor.

**İnternete açılınca zorunlu hale gelenler (Faz B)**
- **Auth:** `notes.md` #4 kapsamında ayrıca yapılıyor. Faz B'nin ön koşulu: auth bitmeden sunucu dışarı açılmaz.
- **HTTPS reverse proxy:** Caddy (otomatik Let's Encrypt, küçük config, ~20–40 MB). Dışarıya yalnızca 80 ve 443 açık olur; backend'in 8000 portu ve `/metrics`, Grafana, Jaeger host'a açılmaz (Grafana gerekiyorsa SSH tüneli veya auth'lu bir alt yol üzerinden).
- **Rate limit:** IP bazlı, Caddy'de veya backend'de. Global token sınırı tek bir kişinin kotayı tüketmesini engellemiyor; auth geldikten sonra kullanıcı bazlı token sınırı ayrıca değerlendirilecek (`services/usage.py`).
- **`/static-pdfs`:** auth'suz StaticFiles yerine auth kontrolü yapan bir endpoint. Frontend'de `PdfViewer` URL'si buna göre güncellenir.
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
- Auth'suz istekte 401, rate limit aşımında 429 dönüyor mu (frontend 429'u token sınırı sanıyor; ayrı bir kod veya mesaj gerekebilir, `services/api.ts`).
- Port taraması: dışarıdan yalnızca 22, 80 ve 443 açık mı.
- Restart sonrası verinin kaybolmadığı kontrol edilir.

---

## Yol haritası (öncelik sırasıyla)

Etiketler: 🔴 Production için gerekli · 🟡 Faydalı iyileştirme · ⚪ İleriye dönük / opsiyonel

| Faz | İş | Sınıf | Değişecek dosyalar | Risk | Ek RAM |
|---|---|---|---|---|---|
| 0a | Upload'da yarım yazmayı geri alma (chunk rollback) + regresyon testi | 🔴 | `services/ingestion.py`, `main.py`, `tests/test_upload.py` | Düşük; geri alma sırasında da hata olursa loglanmalı | 0 |
| 0b | Soru metnini loglardan çıkarma, upload'da tam yol yerine ad | 🔴 | `services/query.py`, `main.py` | Yok | 0 |
| 1 | JSON logging + Request ID middleware + CORS header'ları | 🔴 | yeni `core/logging.py`, `main.py`, `core/settings.py` (`LOG_FORMAT`, `SERVICE_NAME`), `.env.example`, `docs/configuration.md`, yeni test | uvicorn logger'larının çift basılması; contextvar'ın thread'e geçmemesi (testle kontrol) | ~0 |
| 2 | Health/readiness endpoint'leri + Docker prod/dev ayrımı + healthcheck + graceful shutdown | 🔴 | `main.py`, `Dockerfile`, `docker-compose.yml`, yeni `docker-compose.override.yml`, `docs/` | Bugünkü `docker compose up` akışının bozulması (override bunu önler); chroma mount yolunun doğru olması | 0 |
| — | **Auth** (`notes.md` #4, ayrı takip ediliyor) | 🔴 | notes.md'de | — | — |
| B | **Bulut hazırlığı:** Caddy (HTTPS), rate limit, korumalı PDF endpoint'i, kapalı portlar, proxy header'ları, prod CORS (§15) | 🔴 (internete açılmadan önce) | yeni `deploy/Caddyfile`, `docker-compose.yml` (veya `docker-compose.prod.yml`), `main.py`, `rag-frontend/app/page.tsx` + `PdfViewer`, `services/api.ts`, `docs/`, test | Frontend'in PDF URL'sinin değişmesi; rate limit 429'unun token 429'uyla karışması | ~30 MB (Caddy) |
| B2 | **Deploy:** GHCR'a imaj push eden workflow, sunucu kurulumu, veri kopyalama, yedekleme | 🔴 (bulut için) | yeni `.github/workflows/deploy.yml` (veya yalnızca build+push), `docs/deployment.md` | Secret yönetimi; ilk veri taşıma | – |
| 3 | Sentry (backend) + scrubber + beklenen/beklenmeyen hata ayrımı | 🔴 | yeni `core/observability.py`, `main.py`, `services/query.py` (beklenen hatalar), `core/settings.py`, `requirements.txt`, test | Çift event; local variable sızıntısı (kapatılacak) | ~20 MB |
| 4 | Prometheus metrikleri (`/metrics`) | 🔴 | `core/observability.py`, `main.py`, `services/query.py`, `services/ingestion.py`, `requirements.txt`, test | Kardinalite; search ayrımının skorları bozması (ilk aşamada ayrılmayacak) | ~10 MB |
| 5 | Gemini circuit breaker + readiness'e bağlanması | 🟡 | yeni `services/circuit_breaker.py`, `services/query.py`, `main.py`, test | Yanlış ayarlanırsa gereksiz 503; eşikler `.env`'den | 0 |
| 6 | Prometheus + Grafana (yerelde profil, bulutta varsayılan) + provisioned dashboard | 🟡 | `docker-compose.yml`, yeni `monitoring/` (prometheus.yml, grafana provisioning, dashboard JSON) | RAM; Grafana'nın dışarı açılmaması | 200–350 MB |
| 7 | OpenTelemetry (manuel span + FastAPI/httpx) + Jaeger profili | 🟡/⚪ | `core/observability.py`, `services/query.py`, `services/ingestion.py`, `requirements-otel.txt`, compose | Prompt'un attribute'a sızması (kural + test); dependency sayısı | 30–50 MB + Jaeger (opt-in) |
| 8 | Frontend: `X-Request-ID` gönderme ve hata mesajında gösterme, fetch timeout (AbortController), `error.tsx` | 🟡 | `rag-frontend/services/api.ts`, `app/error.tsx` | Düşük | 0 |
| 9 | Sentry (frontend, `@sentry/nextjs`) | ⚪ | `rag-frontend/` config dosyaları | Build ağırlaşır | 0 (backend) |
| 10 | Frontend container'ı (standalone) compose'a; bulutta frontend de sunucuda olacaksa Faz B2'ye taşınır | 🟡 (bulut) / ⚪ | yeni `rag-frontend/Dockerfile`, compose | Build-time env | ~150 MB |
| 11 | Chroma server mode + ayrı ingestion worker (öğrenme denemesi, DB **kopyasında**) | ⚪ | çok sayıda | Yüksek: RAM, tutarlılık, yeniden mimari | +1.3–2 GB |

**Her fazda süreç:** uygulamadan önce kısa plan ve onay → uygulama → `ruff check .` + `python -m unittest discover tests` → (Docker fazlarında) build + healthcheck + smoke test → değişen dosyalar, test çıktısı ve kalan sorunlarla rapor.

## Onay bekleyen kararlar

1. Mikroservise **bölmeme** (modüler monolit + gözlemlenebilirlik) önerisi kabul mü?
2. Faz 0a/0b (bulunan iki hata) gözlemlenebilirlikten önce yapılsın mı?
3. Sentry için SaaS hesabı (ücretsiz plan) kullanılacak mı? Self-hosted Sentry ~4 GB+ RAM istediği için önerilmez.
4. OTel opsiyonel bağımlılık olarak mı tutulsun, ana `requirements.txt`'e mi girsin?
5. Bulut sağlayıcı ve bölge hangisi olacak (KVKK açısından AB bölgesi)? Makine boyutu 4 vCPU / 8 GB uygun mu?
6. Frontend nerede barındırılacak: aynı VPS'te container olarak mı, Vercel gibi bir statik host'ta mı?
7. Domain var mı? (Caddy ile HTTPS için gerekli.)
