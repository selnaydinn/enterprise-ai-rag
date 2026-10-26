import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from core.settings import get_settings

#Token kullanımı şimdilik global (kullanıcı bazlı sınır auth ile gelecek); restart'ta kaybolmasın diye SQLite'ta tutuluyor
#Değerler core/settings.py'den (.env); sayaç dosyası yoksa ilk kullanımda oluşturulur
_settings = get_settings()
USAGE_DB_PATH = _settings.usage_db_path
TOKEN_LIMIT = _settings.token_limit
#Sınır saate bağlı pencerelerle sıfırlanır (6 saatte: 00:00, 06:00, 12:00, 18:00); 24'ü bölmesi Settings'te kontrol ediliyor
WINDOW_HOURS = _settings.token_window_hours

#Türkiye 2016'dan beri yıl boyu UTC+3; zoneinfo Windows'ta ek paket (tzdata) istediği için sabit ofset kullanıyoruz
TURKEY_TZ = timezone(timedelta(hours=3))


#sqlite3'ün "with conn" bloğu commit eder ama bağlantıyı kapatmaz; kapatmayı biz yapıyoruz
@contextmanager
def _connect():
    conn = sqlite3.connect(USAGE_DB_PATH, timeout=10)
    try:
        with conn:
            conn.execute("CREATE TABLE IF NOT EXISTS token_usage (window_start INTEGER PRIMARY KEY, tokens INTEGER NOT NULL)")
            yield conn
    finally:
        conn.close()

#İçinde bulunulan pencerenin başlangıcı ve sıfırlanma anı (Türkiye saatiyle)
def window_bounds(now=None):
    local = (now or datetime.now(timezone.utc)).astimezone(TURKEY_TZ)
    start = local.replace(hour=local.hour - local.hour % WINDOW_HOURS, minute=0, second=0, microsecond=0)
    return start, start + timedelta(hours=WINDOW_HOURS)

#Kırpma yok: son istek sınırı biraz aşabilir, gerçek tüketim olduğu gibi kaydedilir
#Tek UPSERT sorgusu atomik olduğu için eşzamanlı isteklerde sayım kaybolmaz
def record_usage(tokens, now=None):
    if tokens <= 0:
        return
    start, _ = window_bounds(now)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO token_usage (window_start, tokens) VALUES (?, ?) "
            "ON CONFLICT(window_start) DO UPDATE SET tokens = tokens + excluded.tokens",
            (int(start.timestamp()), tokens),
        )

#Yeni pencerenin satırı olmadığı için kullanım kendiliğinden 0'dan başlar; ayrı bir sıfırlama kodu yok
def get_status(now=None):
    start, end = window_bounds(now)
    with _connect() as conn:
        row = conn.execute("SELECT tokens FROM token_usage WHERE window_start = ?", (int(start.timestamp()),)).fetchone()
    used = row[0] if row else 0
    return {
        "used": used,
        "limit": TOKEN_LIMIT,
        "resets_at": end.isoformat(),
        "limited": used >= TOKEN_LIMIT
    }
