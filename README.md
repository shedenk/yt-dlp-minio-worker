# YT-DLP MinIO Worker with Whisper Transcription

Sistem backend yang scalable untuk mengunduh video YouTube, mengekstrak audio, dan menghasilkan transkripsi menggunakan model OpenAI Whisper. Sistem ini menggunakan Redis untuk antrian pekerjaan (job queue) dan MinIO untuk penyimpanan objek.

## Fitur Utama

- **Download Video & Audio**: Mendukung format video, audio saja, atau keduanya.
- **Transkripsi AI**: Menggunakan `faster-whisper` (mendukung GPU/CPU) untuk speech-to-text.
- **Manajemen Subtitle Cerdas**:
  - **Download Subtitle**: Mengambil subtitle bawaan YouTube (Manual/CC) jika tersedia.
  - **Generate Subtitle**: Membuat subtitle baru via Whisper jika tidak ada subtitle bawaan.
  - **Deteksi Awal**: Endpoint `check_channel` kini dapat mendeteksi ketersediaan subtitle (`has_subtitles`) sebelum download.
- **Penyimpanan**: Integrasi otomatis dengan MinIO (S3 Compatible).
- **Monitoring Channel**: Memantau channel YouTube untuk video baru.

## Instalasi & Konfigurasi

Pastikan Anda memiliki Python 3.10+, Redis, dan (Opsional) MinIO server.

### Environment Variables

Buat file `.env` atau set environment variables berikut:

```bash
# Redis Configuration
REDIS_URL=redis://localhost:6379/0

# MinIO Configuration (Opsional)
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=mybucket
MINIO_SECURE=false
MINIO_PUBLIC_BASE_URL=http://localhost:9000/mybucket

# Worker Settings
DOWNLOAD_DIR=/data/downloads
COOKIES_PATH=/data/cookies/cookies.txt
WORKER_CONCURRENCY=3
WHISPER_MODEL=base    # tiny, base, small, medium, large-v2
USE_GPU=true          # Set false untuk CPU only
```

## Penggunaan API

Jalankan server API:

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

### 1. Enqueue Download (Menambahkan Antrian)

Endpoint: `POST /enqueue`

```json
{
  "url": "https://www.youtube.com/watch?v=VIDEO_ID",
  "video": true, // Download video
  "audio": false, // Download audio
  "transcribe": true, // Transkrip (Bahasa Indonesia)
  "callback_url": "http...", // Webhook URL untuk notifikasi selesai
  "db_id": "123"
}
```

### 2. Check Channel (Cek Video Baru)

Endpoint: `POST /check_channel`

Memeriksa video terbaru di channel dan melihat status subtitle-nya.

```json
{
  "channel_url": "https://www.youtube.com/channel/CHANNEL_ID",
  "limit": 5,
  "track": false, // Set true untuk otomatis download video baru
  "include_subs": true // Parameter untuk worker jika track=true
}
```

**Contoh Response:**
Perhatikan field `has_subtitles` yang menunjukkan apakah video tersebut memiliki subtitle di YouTube.

```json
{
  "new_count": 1,
  "video_urls": [
    {
      "url": "https://www.youtube.com/watch?v=...",
      "title": "Judul Video",
      "upload_date": "20240101",
      "has_subtitles": true, // true jika ada subtitle manual/auto di YouTube
      "duration": 1250
    }
  ]
}
```

## Menjalankan Worker

Worker bertugas memproses antrian dari Redis.

```bash
python worker.py
```

Worker mendukung multiprocessing (diatur via `WORKER_CONCURRENCY`) dan memiliki mekanisme retry otomatis jika download gagal.
