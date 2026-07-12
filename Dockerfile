FROM python:3.10-slim

# Çalışma klasörünü sanal makine içinde /app olarak belirliyoruz
WORKDIR /app

# Bağımlılıkları kopyalayıp doğrudan yüklüyoruz (apt-get adımını es geçtik)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Proje dosyalarını (kodlar, data, chroma_db) içeri alıyoruz
COPY . .

# FastAPI sunucumuzun dış dünyaya açılacağı kapı
EXPOSE 8000

# Konteyner başladığında otomatik olarak FastAPI'yi (main.py) ayağa kaldıracak komut
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]