FROM python:3.11-slim

WORKDIR /app

# Install ffmpeg (wajib untuk gabung audio+video)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    curl && \
    rm -rf /var/lib/apt/lists/*

# Install dependencies Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy aplikasi
COPY . .

# Buat folder untuk cookie
RUN mkdir -p /app/cookies
VOLUME ["/app/cookies"]

EXPOSE 3000

CMD ["python", "app.py"]