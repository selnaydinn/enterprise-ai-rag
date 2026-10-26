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

# Proje dosyalarını (kodlar, data, chroma_db) içeri alıyoruz
COPY . .

# FastAPI sunucumuzun dış dünyaya açılacağı kapı
EXPOSE 8000

# Konteyner başladığında otomatik olarak FastAPI'yi (main.py) ayağa kaldıracak komut
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]