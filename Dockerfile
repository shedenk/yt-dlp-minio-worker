FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    cron \
    ca-certificates \
    curl \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y nodejs \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py worker.py cleanup.py ./

RUN echo "*/30 * * * * python /app/cleanup.py >> /var/log/cleanup.log 2>&1" > /etc/cron.d/cleanup \
 && chmod 0644 /etc/cron.d/cleanup

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
