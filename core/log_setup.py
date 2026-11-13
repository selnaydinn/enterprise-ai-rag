#Log biçimi ve request ID (notes2.md Faz 1, §7)
#Dosya adı "logging.py" değil: Python'ın kendi logging modülüyle aynı ad onu gölgeleyebilir
import json
import logging
import re
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

from starlette.datastructures import Headers, MutableHeaders

REQUEST_ID_HEADER = "X-Request-ID"
#Dışarıdan gelen ID yalnızca bu biçimdeyse kullanılır; satır sonu vb. ile loga sahte satır eklenemesin (log injection)
VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9-]{1,64}")
#İstek dışındaki loglarda (açılış, model yükleme) görünen değer
NO_REQUEST_ID = "-"

TEXT_FORMAT = "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"
TEXT_DATEFMT = "%Y-%m-%d %H:%M:%S"

#Her isteğin kendi kutusu: aynı anda gelen iki istek birbirinin ID'sini görmez
#Starlette sync endpoint'leri ve run_in_threadpool'u thread'de çalıştırırken kutunun kopyasını thread'e taşıyor
#(tests/test_log_setup.py ile doğrulanıyor)
request_id_var: ContextVar[str] = ContextVar("request_id", default=NO_REQUEST_ID)

logger = logging.getLogger("EnterpriseAI")


def get_request_id() -> str:
    return request_id_var.get()


#Her log kaydına o anki isteğin ID'si eklenir; formatter'lar %(request_id)s / record.request_id ile kullanır
#Handler'a takılıyor (logger'a değil): uvicorn gibi başka logger'lardan gelen kayıtlar da ID alsın
class RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = request_id_var.get()
        return True


#Bir satır = bir JSON nesnesi; Sentry, Loki gibi araçlar alanlara göre süzebilir
class JsonFormatter(logging.Formatter):
    def format(self, record):
        data = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", NO_REQUEST_ID),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        #Türkçe karakterler ş yerine ş olarak yazılsın
        return json.dumps(data, ensure_ascii=False)


#main.py'deki eski basicConfig'in yerine geçer; tekrar çağrılırsa handler'ı değiştirir (çift satır olmaz)
#fmt: "text" (okunaklı, varsayılan) veya "json"
def setup_logging(level: str, fmt: str) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RequestIdFilter())
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(TEXT_FORMAT, datefmt=TEXT_DATEFMT))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    #uvicorn'un kendi handler'ları kaldırılır, kayıtları bizim handler'a akar: aynı biçim ve request ID
    #(uvicorn.error, "uvicorn"a akıyor). Seviyeleri uvicorn'un ayarında kalır
    for name in ("uvicorn", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True


#Her isteğe bir ID verir, cevaba X-Request-ID başlığını ekler ve istek bitince tek satırlık özet loglar
#Saf ASGI middleware: isteği aynı task'te çalıştırır, böylece ID beklenmeyen hata yakalayıcısında da görünür
#Beklenmeyen hatada cevap bu katmanın dışında (Starlette ServerErrorMiddleware) üretildiği için başlık oraya
#main.py'deki unhandled_exception_handler'da elle ekleniyor
class RequestIdMiddleware:

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(REQUEST_ID_HEADER)
        request_id = incoming if incoming and VALID_REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        #reset edilmiyor: her istek kendi task'inde (kendi kutu kopyasıyla) çalışıyor, sonraki isteğe sızmaz;
        #reset edilseydi dıştaki hata yakalayıcısı ID'yi göremezdi
        request_id_var.set(request_id)

        start = time.perf_counter()
        status = 500

        async def send_with_request_id(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            level = logging.WARNING if status >= 500 else logging.INFO
            logger.log(level, f"{scope['method']} {scope['path']} {status} ({elapsed_ms:.0f} ms)")
