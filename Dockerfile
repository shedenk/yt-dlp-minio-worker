# Stage 1: Builder
FROM python:3.12-slim as builder

WORKDIR /app

# Install build dependencies (git, compiler) hanya di stage ini
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Runtime (Final Image)
FROM python:3.12-slim

WORKDIR /app

# Install runtime dependencies only
# Menggunakan --no-install-recommends untuk mengurangi ukuran (terutama ffmpeg)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    cron \
    ca-certificates \
    libgomp1 \
    nodejs \
 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local
COPY app.py worker.py cleanup.py ./

RUN echo "*/30 * * * * python /app/cleanup.py >> /var/log/cleanup.log 2>&1" > /etc/cron.d/cleanup \
 && chmod 0644 /etc/cron.d/cleanup

# Healthcheck menggunakan Python standard library (urllib) untuk menghindari install curl.
# Jika ROLE=worker, dianggap sehat (exit 0). Jika API, cek endpoint /health.
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import os, sys, urllib.request; sys.exit(0) if os.getenv('ROLE') == 'worker' else urllib.request.urlopen('http://localhost:8080/health')"

EXPOSE 8080
CMD ["bash", "-c", "\
rm -rf /data/downloads/* 2>/dev/null; \
if [ \"$ROLE\" = \"worker\" ]; then \
  echo 'Starting YT-DLP WORKER'; \
  python /app/worker.py; \
else \
  echo 'Starting YT-DLP API'; \
  uvicorn app:app --host 0.0.0.0 --port 8080; \
fi"]
