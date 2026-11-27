from prometheus_client import Counter, Gauge, Histogram, disable_created_metrics, start_http_server

#PROMETHEUS METRİKLERİ (notes2.md Faz 4)
#Değerler yalnızca bu işlemin belleğinde tutulur; uygulama yeniden başlayınca sıfırlanır (Prometheus bunu anlıyor)
#Prometheus METRICS_PORT'taki /metrics adresini düzenli aralıklarla okur (core/settings.py metrics_port)
#Etiketlere yalnızca sabit ve az sayıda değer yazılır: soru metni, dosya adı, kullanıcı, IP, istek ID'si asla
#(gizlilik; ayrıca her farklı değer yeni bir satır açar, satır sayısı sınırsız büyür)
#Tek işlem (uvicorn worker) varsayımıyla çalışır; birden fazla worker'da kütüphanenin çoklu işlem modu gerekir

#Kütüphane her sayaç için "ne zaman oluşturuldu" bilgisini ayrı bir _created satırı olarak basıyor; gereksiz, kapatıldı
#Metrikler tanımlanmadan önce çağrılmalı
disable_created_metrics()

#Kovalar (saniye): histogram süreleri tek tek değil, "bu kadar veya daha kısa sürenler" olarak sayar
#HTTP: hızlı istekler (liste, giriş) · soru cevapları · 40 sn backend / 90 sn frontend sınırı · PDF yükleme (dakikalar)
HTTP_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 90, 300, 900)
#Aşamalar (arama, rerank, LLM) yükleme içermiyor; 90 sn'den uzun süren aşama zaten zaman aşımına uğramış olur
STAGE_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 90)

# HTTP (core/log_setup.py RequestIdMiddleware)
HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Gelen HTTP istekleri (başarılı sağlık kontrolleri hariç)",
    ["method", "route", "status"],
)
HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP isteklerinin süresi (başarılı sağlık kontrolleri hariç)",
    ["method", "route"],
    buckets=HTTP_BUCKETS,
)

# SORU CEVAPLAMA (services/query.py)
RAG_STAGE_DURATION = Histogram(
    "rag_stage_duration_seconds",
    "Soru cevaplama aşamalarının süresi (search: embedding + Chroma, rerank, llm: Gemini veya sahte LLM)",
    ["stage"],
    buckets=STAGE_BUCKETS,
)
#reason: e5_threshold, rerank_threshold, bilgi_yok, empty_answer, empty_db, unexpected_empty (beklenmeyen, arıza işareti)
RAG_NO_RESULT = Counter(
    "rag_no_result_total",
    "Cevapsız kalan sorular, sebebe göre",
    ["reason"],
)
#kind: timeout, quota, server, connection (services/query.py classify_llm_error)
LLM_ERRORS = Counter(
    "llm_errors_total",
    "Başarısız LLM çağrıları, türe göre",
    ["kind"],
)
#Yalnızca Gemini modunda; sahte LLM'in token sayıları uydurma olduğu için sayılmaz
LLM_TOKENS = Counter(
    "llm_tokens_total",
    "Gemini'nin harcadığı token'lar (input: girdi, thinking: düşünme, output: cevap)",
    ["kind"],
)
#Aktif mod 1, diğeri 0; sunucunun yanlışlıkla sahte modda açık kalması buradan görülür
RAG_LLM_MODE = Gauge(
    "rag_llm_mode",
    "Aktif LLM modu (1 = aktif)",
    ["mode"],
)

LLM_MODES = ("gemini", "fake")
#timings sözlüğündeki adlar (services/query.py) → stage etiketi; LLM aşamasının adı moda göre değişiyor (Gemini / Sahte LLM)
STAGE_LABELS = {"arama": "search", "rerank": "rerank"}
#gemini_token_breakdown'daki adlar → kind etiketi
TOKEN_LABELS = {"girdi": "input", "düşünme": "thinking", "cevap": "output"}


#Adres şablonu: /documents/abc.pdf değil /documents/{name} (dosya adı etikete girmesin)
#FastAPI isteği bir endpoint'e eşleştirince scope'a "route" ekliyor; eşleşme yoksa (404, CORS'un kendi cevapladığı
#OPTIONS ön kontrolü) "unmatched"
def route_label(scope):
    route = scope.get("route")
    return getattr(route, "path", None) or "unmatched"


def record_http_request(scope, status, seconds):
    method = scope["method"]
    route = route_label(scope)
    HTTP_REQUESTS.labels(method=method, route=route, status=str(status)).inc()
    HTTP_DURATION.labels(method=method, route=route).observe(seconds)


#Çalışmayan aşama (None) ölçülmez; hata olsa da o ana kadar ölçülen süre yazılır (log_timings ile aynı sayılar)
def record_stage_timings(timings):
    for name, seconds in timings.items():
        if seconds is not None:
            RAG_STAGE_DURATION.labels(stage=STAGE_LABELS.get(name, "llm")).observe(seconds)


def record_no_result(reason):
    RAG_NO_RESULT.labels(reason=reason).inc()


def record_llm_error(kind):
    LLM_ERRORS.labels(kind=kind).inc()


def record_tokens(tokens):
    for name, count in tokens.items():
        if name in TOKEN_LABELS and count > 0:
            LLM_TOKENS.labels(kind=TOKEN_LABELS[name]).inc(count)


def set_llm_mode(mode):
    for name in LLM_MODES:
        RAG_LLM_MODE.labels(mode=name).set(1 if name == mode else 0)


#Arka planda ayrı bir thread'de /metrics sunar (uygulamanın kendisiyle aynı işlem: sayaçlar bu işlemin belleğinde)
#0.0.0.0: Docker'da Prometheus container'ı aynı ağdan ulaşsın; port dışarıya yayınlanmadığı için dışarıdan erişilmez
#Döndürülen sunucu kapanışta stop_metrics_server'a verilir
def start_metrics_server(port):
    server, thread = start_http_server(port)
    return server, thread


def stop_metrics_server(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
