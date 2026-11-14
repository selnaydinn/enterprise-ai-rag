#Yerel venv ile aynı Python sürümü; testler bu sürümde doğrulanıyor
FROM python:3.13-slim

# Çalışma klasörünü sanal makine içinde /app olarak belirliyoruz
WORKDIR /app

# Bağımlılıkları kopyalayıp doğrudan yüklüyoruz (apt-get adımını es geçtik)
COPY requirements.txt .
# Proje GPU kullanmıyor; önce yalnızca torch'u CPU sürümüyle kuruyoruz (image birkaç GB küçülür)
# Diğer paketler normal PyPI'dan gelir; torch zaten kurulu olduğu için ikinci adım onu atlar
# Torch sürümü requirements.txt'deki ile aynı olmalı
RUN pip install --no-cache-dir torch==2.12.1 --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

# Uygulamayı çalıştıracak sınırlı kullanıcı (root değil; notes2.md Faz 2, 4d). uid 1000: Linux sunucuda veri
# klasörlerinin sahibi de bu numara yapılır
RUN useradd --create-home --uid 1000 appuser

# Kodu imaja kopyalıyoruz; veri (DB, PDF'ler, data/usage.db), .env ve venv .dockerignore ile dışarıda kalır
# Veriler docker-compose.yml'de dışarıdan bağlanıyor
# Kod root'a ait kalır: appuser kodu okuyabilir ama değiştiremez (en az yetki)
COPY . .

# appuser'ın yazabildiği iki klasör (imajın içinde); dışarıdan bağlanan klasörlerin izinleri host'tan gelir
#   /app/data: usage.db (SQLite yanına geçici dosya yazdığı için tek dosya değil klasör)
#   /models:   model önbelleği; boş bir hf_cache volume'u bu sahiplikle başlar (production'da salt okunur bağlanır)
RUN mkdir -p /app/data /models && chown appuser:appuser /app/data /models
USER appuser

# FastAPI sunucumuzun dış dünyaya açılacağı kapı
EXPOSE 8000

# Docker 30 sn'de bir uygulamaya "iş yapabilir misin?" diye sorar (main.py /health/ready; Gemini'ye gitmez)
# 3 kez üst üste cevap gelmezse veya 503 dönerse docker ps "unhealthy" gösterir
# start-period: açılışta modeller ~30-60 sn yükleniyor; bu sürede başarısız kontroller sayılmaz
# İmajda curl yok; kontrol Python ile yapılıyor (503 veya bağlantı hatası → çıkış kodu 1)
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/ready', timeout=4)" || exit 1

# Konteyner başladığında otomatik olarak FastAPI'yi (main.py) ayağa kaldıracak komut
# --reload yok (production); geliştirmede docker-compose.override.yml --reload ile değiştiriyor
# Düzgün kapanma: kapatma sinyalinde yeni istek alınmaz, süren işler (soru, PDF yükleme) en fazla 110 sn beklenir.
# Docker'ın bekleme süresinden (docker-compose.yml stop_grace_period: 120s) kısa: süre dolarsa uygulamayı uvicorn
# kapatır, Docker zorla öldürmez. Zorla öldürülen upload'da geri alma (rollback) çalışamaz, yarım parçalar kalır
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "110"]