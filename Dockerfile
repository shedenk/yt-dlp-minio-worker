FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    cron \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py cleanup.py ./

# Cron tiap 30 menit
RUN echo "*/30 * * * * python /app/cleanup.py >> /var/log/cleanup.log 2>&1" > /etc/cron.d/cleanup \
 && chmod 0644 /etc/cron.d/cleanup \
 && crontab /etc/cron.d/cleanup

CMD cron && python app.py
