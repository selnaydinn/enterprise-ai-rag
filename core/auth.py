import hashlib
import hmac
import logging
import threading
import time
from fastapi import HTTPException, Request, Response
from core.settings import get_settings

logger = logging.getLogger("EnterpriseAI")

#KİMLİK DOĞRULAMA (tek kullanıcı)
#Akış: POST /auth/login'de parola doğruysa backend imzalı bir oturum token'ı üretir ve tarayıcıya cookie olarak verir;
#tarayıcı bu cookie'yi sonraki her isteğe kendisi ekler, require_auth her istekte imzayı ve süreyi kontrol eder
#Stateless: oturumlar sunucuda saklanmıyor, yalnızca imza kontrol ediliyor. Restart'ta oturum düşmez; ama tek bir cookie
#iptal edilemez (çıkış yalnızca o tarayıcıdaki cookie'yi siler). Acil durumda SESSION_SECRET değiştirilir: tüm oturumlar kapanır

SESSION_COOKIE = "session"

#Hatalı giriş sınırı: MAX_FAILED_ATTEMPTS yanlış denemeden sonra LOCKOUT_SECONDS boyunca giriş denenemez (brute force)
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60


#Başlangıçta (main.py lifespan) çağrılır; auth açıkken parola veya secret eksikse uygulama başlamaz
#Fail-closed: ayar unutulursa sistem "auth'suz açılayım" demez, hata verip durur
def check_auth_config():
    settings = get_settings()
    if not settings.auth_enabled:
        logger.warning("Kimlik doğrulama KAPALI (AUTH_ENABLED=false). Yalnızca yerel geliştirme için kullanın.")
        return
    missing = [name for name, value in (("APP_PASSWORD", settings.app_password),
                                        ("SESSION_SECRET", settings.session_secret)) if value is None]
    if missing:
        raise RuntimeError(f"{', '.join(missing)} tanımlı değil (AUTH_ENABLED=true). .env.example dosyasına bakın.")
    logger.info(f"Kimlik doğrulama açık (oturum süresi: {settings.session_hours} saat).")


# PAROLA
#İki tarafın SHA-256 özeti karşılaştırılıyor: compare_digest uzunlukları farklı girdilerde hemen False döner; özetler
#hep 32 bayt olduğu için parolanın uzunluğu da cevap süresinden anlaşılamaz
#compare_digest her baytı karşılaştırır, ilk farkta durmaz (timing attack: normal == ilk farklı karakterde durur ve
#cevap süresinden parola harf harf tahmin edilebilir)
def check_password(candidate):
    expected = get_settings().app_password.get_secret_value()
    return hmac.compare_digest(hashlib.sha256(candidate.encode()).digest(),
                               hashlib.sha256(expected.encode()).digest())


# OTURUM TOKEN'I
#Biçim: "<bitiş zamanı (unix sn)>.<HMAC-SHA256 imzası>"; ör. "1731400000.a8f3c9..."
#İmza yalnızca SESSION_SECRET'ı bilen (backend) tarafından üretilebilir; bitiş zamanı değiştirilirse imza tutmaz
def _sign(payload):
    key = get_settings().session_secret.get_secret_value().encode()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()

def create_session_token(now=None):
    expires = int((now if now is not None else time.time()) + get_settings().session_hours * 3600)
    payload = str(expires)
    return f"{payload}.{_sign(payload)}"

#Biçim bozuk, imza tutmuyor veya süresi dolmuşsa False
def verify_session_token(token, now=None):
    payload, sep, signature = token.partition(".")
    #isascii: "²" gibi Unicode rakamlar isdigit'ten geçer ama int() hata verir
    if not sep or not payload.isascii() or not payload.isdigit():
        return False
    if not hmac.compare_digest(signature, _sign(payload)):
        return False
    return int(payload) > (now if now is not None else time.time())


# COOKIE
#Ayarlar tek yerde: silerken de aynı path/samesite/secure verilmeli, yoksa tarayıcı cookie'yi silmez
#HttpOnly: JavaScript okuyamaz (sayfada bir açık olsa bile cookie çalınamaz)
#SameSite=Lax: başka bir siteden başlatılan POST/DELETE isteklerine eklenmez (CSRF)
#Secure: yalnızca HTTPS'te gönderilir (COOKIE_SECURE; yerelde http olduğu için kapalı)
def set_session_cookie(response: Response):
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(),
        max_age=settings.session_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )

def clear_session_cookie(response: Response):
    settings = get_settings()
    response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="lax", secure=settings.cookie_secure, path="/")


# ENDPOINT KORUMASI
#Korunan endpoint'lere dependencies=[Depends(require_auth)] ile eklenir; FastAPI endpoint'ten önce çalıştırır
#Cookie yoksa veya geçersizse 401 fırlatır, endpoint hiç çalışmaz (Gemini çağrılmaz, token harcanmaz)
#401: "seni tanımıyorum, giriş yap" · 403: "tanıyorum ama izin yok" (ör. ALLOW_DELETE=false)
def require_auth(request: Request):
    if not get_settings().auth_enabled:
        return
    token = request.cookies.get(SESSION_COOKIE)
    if not token or not verify_session_token(token):
        raise HTTPException(status_code=401, detail="Oturum açmanız gerekiyor.")


# HATALI GİRİŞ SINIRI
#İstemci (IP) bazında: tek bir global sayaç olsaydı, başkası yanlış parola deneyerek sizi de sürekli kilitleyebilirdi
#Bulutta reverse proxy arkasında gerçek IP için uvicorn --proxy-headers gerekir (notes2.md Faz B)
#Sayaç bellekte: restart'ta sıfırlanır (tek kullanıcı ve tek worker için yeterli)
#Son hatadan LOCKOUT_SECONDS geçince sayaç sıfırlanır; dünkü 4 hata bugünkü 1 hatayla kilitlemesin
class LoginGuard:
    #Saldırı altında sözlük sınırsız büyümesin; bu sayıyı aşınca süresi geçmiş kayıtlar temizlenir
    MAX_TRACKED_CLIENTS = 1000

    def __init__(self, max_failures=MAX_FAILED_ATTEMPTS, lockout_seconds=LOCKOUT_SECONDS, clock=time.monotonic):
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self._clock = clock
        #istemci -> [hata sayısı, son hata anı, kilidin bittiği an]
        self._clients = {}
        #Login endpoint'i thread'de çalışabilir; eşzamanlı denemeler sayacı bozmasın
        self._lock = threading.Lock()

    #Kilitliyse kalan saniye, değilse 0
    def locked_seconds(self, client):
        with self._lock:
            entry = self._clients.get(client)
            if entry is None:
                return 0
            return max(0, int(entry[2] - self._clock()))

    def record_failure(self, client):
        with self._lock:
            now = self._clock()
            if len(self._clients) >= self.MAX_TRACKED_CLIENTS:
                self._prune(now)
            count, last, locked_until = self._clients.get(client, [0, now, 0])
            if now - last > self.lockout_seconds:
                count = 0
            count += 1
            if count >= self.max_failures:
                locked_until = now + self.lockout_seconds
                count = 0
                logger.warning(f"Çok sayıda hatalı giriş denemesi; giriş {self.lockout_seconds // 60} dakika kilitlendi.")
            self._clients[client] = [count, now, locked_until]

    #Başarılı girişte o istemcinin sayacı silinir
    def record_success(self, client):
        with self._lock:
            self._clients.pop(client, None)

    def reset(self):
        with self._lock:
            self._clients.clear()

    def _prune(self, now):
        self._clients = {c: e for c, e in self._clients.items()
                         if e[2] > now or now - e[1] <= self.lockout_seconds}


login_guard = LoginGuard()
