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
- **Webhook Callback**: Notifikasi otomatis ke endpoint eksternal saat job selesai.
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
MAX_RETRIES=3
JOB_TIMEOUT=7200      # 2 hours
```

## Penggunaan API

Jalankan server API:

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

### 1. Enqueue Download (Menambahkan Antrian)

**Endpoint:** `POST /enqueue`

**Request Body:**

```json
{
  "url": "https://www.youtube.com/watch?v=VIDEO_ID",
  "video": true,
  "audio": false,
  "transcribe": true,
  "callback_url": "https://your-domain.com/webhook/endpoint",
  "db_id": "123"
}
```

**Parameter:**
- `url` (string, required): URL video YouTube
- `video` (boolean, default: true): Download video dalam format MP4
- `audio` (boolean, default: false): Download audio dalam format MP3
- `transcribe` (boolean, default: false): **Generate transkripsi menggunakan Whisper AI ke bahasa Indonesia** (bukan download subtitle dari YouTube)
- `callback_url` (string, optional): URL webhook untuk menerima notifikasi saat job selesai
- `db_id` (string, optional): ID custom untuk tracking di database Anda

**Catatan Penting:**
- Endpoint ini **TIDAK** download subtitle dari YouTube
- Jika `transcribe=true`, sistem menggunakan **Whisper AI** untuk membuat transkripsi dari audio
- Transkripsi dalam **bahasa Indonesia** dan format **SRT** (dengan timestamp)
- File transkripsi di-upload ke MinIO, URL tersedia di field `transcript_file`

**Response:**

```json
{
  "job_id": "c1ea3e14-4948-461f-acb7-ec4e1974e26c",
  "status": "queued"
}
```

### 2. Check Status (Cek Status Job)

**Endpoint:** `GET /status/{job_id}`

**Response (Processing):**

```json
{
  "status": "downloading (45.2%)",
  "url": "https://www.youtube.com/watch?v=VIDEO_ID",
  "filename": "c1ea3e14-4948-461f-acb7-ec4e1974e26c",
  "format": "",
  "media": "both",
  "audio_format": "mp3",
  "transcribe": "true",
  "include_subs": "true",
  "sub_langs": "all",
  "transcribe_lang": "id",
  "transcribe_prompt": "",
  "callback_url": "https://your-domain.com/webhook/endpoint",
  "db_id": "123",
  "progress": "45.2",
  "retry_count": "0"
}
```

**Response (Completed):**

```json
{
  "status": "done",
  "url": "https://www.youtube.com/watch?v=VIDEO_ID",
  "filename": "c1ea3e14-4948-461f-acb7-ec4e1974e26c",
  "format": "",
  "media": "both",
  "audio_format": "mp3",
  "transcribe": "true",
  "include_subs": "true",
  "sub_langs": "all",
  "transcribe_lang": "id",
  "transcribe_prompt": "",
  "callback_url": "https://your-domain.com/webhook/endpoint",
  "db_id": "123",
  "progress": "100",
  "retry_count": "0",
  "video_file": "http://localhost:9000/mybucket/c1ea3e14-4948-461f-acb7-ec4e1974e26c.mp4",
  "audio_file": "http://localhost:9000/mybucket/c1ea3e14-4948-461f-acb7-ec4e1974e26c.mp3",
  "transcript_file": "http://localhost:9000/mybucket/c1ea3e14-4948-461f-acb7-ec4e1974e26c.srt",
  "duration": 1250,
  "video_duration": 1250,
  "audio_duration": 1250,
  "video_quality": "1080p",
  "video_fps": "30",
  "audio_quality": "128kbps",
  "ext": "mp4"
}
```

**Response (Error):**

```json
{
  "status": "error",
  "url": "https://www.youtube.com/watch?v=VIDEO_ID",
  "error": "Failed after 3 attempts: Video unavailable",
  "last_error": "Video unavailable",
  "retry_count": "3",
  "callback_url": "https://your-domain.com/webhook/endpoint",
  "db_id": "123"
}
```

### 3. Webhook Callback

Ketika job selesai (baik sukses maupun error), sistem akan mengirim POST request ke `callback_url` yang Anda tentukan.

**Callback Request (Success):**

```http
POST https://your-domain.com/webhook/endpoint
Content-Type: application/json

{
  "job_id": "c1ea3e14-4948-461f-acb7-ec4e1974e26c",
  "status": "done",
  "url": "https://www.youtube.com/watch?v=VIDEO_ID",
  "filename": "c1ea3e14-4948-461f-acb7-ec4e1974e26c",
  "format": "",
  "media": "both",
  "audio_format": "mp3",
  "transcribe": "true",
  "include_subs": "true",
  "sub_langs": "all",
  "transcribe_lang": "id",
  "transcribe_prompt": "",
  "callback_url": "https://your-domain.com/webhook/endpoint",
  "db_id": "123",
  "progress": "100",
  "retry_count": "0",
  "video_file": "http://localhost:9000/mybucket/c1ea3e14-4948-461f-acb7-ec4e1974e26c.mp4",
  "audio_file": "http://localhost:9000/mybucket/c1ea3e14-4948-461f-acb7-ec4e1974e26c.mp3",
  "transcript_file": "http://localhost:9000/mybucket/c1ea3e14-4948-461f-acb7-ec4e1974e26c.srt",
  "duration": 1250,
  "video_duration": 1250,
  "audio_duration": 1250,
  "video_quality": "1080p",
  "video_fps": "30",
  "audio_quality": "128kbps",
  "ext": "mp4"
}
```

**Callback Request (Error):**

```http
POST https://your-domain.com/webhook/endpoint
Content-Type: application/json

{
  "job_id": "c1ea3e14-4948-461f-acb7-ec4e1974e26c",
  "status": "error",
  "url": "https://www.youtube.com/watch?v=VIDEO_ID",
  "error": "Failed after 3 attempts: Video unavailable",
  "last_error": "Video unavailable",
  "retry_count": "3",
  "callback_url": "https://your-domain.com/webhook/endpoint",
  "db_id": "123",
  "filename": "c1ea3e14-4948-461f-acb7-ec4e1974e26c",
  "format": "",
  "media": "both",
  "audio_format": "mp3",
  "transcribe": "true"
}
```

**Catatan Penting:**
- Field `heartbeat` tidak disertakan dalam callback payload
- Callback akan dipanggil bahkan jika job gagal (status: "error")
- Timeout untuk callback request adalah 30 detik
- Jika callback gagal, error akan di-log tetapi tidak akan retry

### 4. Check Channel (Cek Video Baru)

**Endpoint:** `POST /check_channel`

Memeriksa video terbaru di channel dan melihat status subtitle-nya. Jika video memiliki subtitle bahasa Indonesia, akan otomatis di-download dan di-upload ke MinIO.

**Request Body:**

```json
{
  "channel_url": "https://www.youtube.com/channel/CHANNEL_ID",
  "limit": 5,
  "track": false
}
```

**Parameter:**
- `channel_url` (string, required): URL channel YouTube
- `limit` (integer, default: 1): Jumlah maksimal video yang akan dikembalikan
- `track` (boolean, default: false): Set `true` untuk otomatis download video baru

**Response:**

```json
{
  "new_count": 2,
  "video_urls": [
    {
      "url": "https://www.youtube.com/watch?v=VIDEO_ID_1",
      "title": "Judul Video 1",
      "upload_date": "20240101",
      "has_subtitles": true,
      "subtitle_url": "http://localhost:9000/mybucket/VIDEO_ID_1.srt",
      "duration": 1250
    },
    {
      "url": "https://www.youtube.com/watch?v=VIDEO_ID_2",
      "title": "Judul Video 2",
      "upload_date": "20240102",
      "has_subtitles": false,
      "subtitle_url": "",
      "duration": 980
    }
  ]
}
```

**Catatan:**
- Field `has_subtitles` menunjukkan apakah video memiliki subtitle manual/auto di YouTube
- Field `subtitle_url` berisi URL MinIO dari subtitle bahasa Indonesia yang sudah di-download (kosong jika tidak ada)
- Subtitle otomatis di-download dan di-upload ke MinIO dengan nama `{video_id}.srt`
- Video dengan durasi < 15 menit akan difilter otomatis
- Video Shorts dan Live Stream akan difilter otomatis

### 5. List Jobs

**Endpoint:** `GET /jobs?limit=20`

Menampilkan daftar job yang ada di queue.

**Response:**

```json
{
  "count": 2,
  "jobs": [
    {
      "job_id": "c1ea3e14-4948-461f-acb7-ec4e1974e26c",
      "status": "processing",
      "url": "https://www.youtube.com/watch?v=VIDEO_ID",
      "progress": "45.2"
    },
    {
      "job_id": "a2bc4d56-7890-1234-5678-90abcdef1234",
      "status": "queued",
      "url": "https://www.youtube.com/watch?v=ANOTHER_ID"
    }
  ]
}
```

### 6. Service Status

**Endpoint:** `GET /service_status`

Memeriksa status kesehatan service (Redis, MinIO, Queue).

**Response:**

```json
{
  "ok": true,
  "role": "api",
  "redis": {
    "ok": true,
    "queue_length": 5
  },
  "minio": {
    "configured": true,
    "ok": true
  }
}
```

## Menjalankan Worker

Worker bertugas memproses antrian dari Redis.

```bash
python worker.py
```

Worker mendukung multiprocessing (diatur via `WORKER_CONCURRENCY`) dan memiliki mekanisme retry otomatis jika download gagal.

**Fitur Worker:**
- Retry otomatis hingga 3x dengan exponential backoff
- Timeout protection (default 2 jam per job)
- Progress tracking real-time
- Automatic cleanup file lokal setelah upload ke MinIO
- Webhook callback otomatis saat job selesai

## Docker Deployment

```bash
docker-compose up -d
```

Service yang akan berjalan:
- **API**: Port 8000
- **Worker**: Background process
- **Redis**: Port 6379
- **MinIO**: Port 9000 (Console: 9001)

## Troubleshooting

### Worker tidak bisa connect ke Redis
Pastikan `REDIS_URL` sudah benar dan Redis service sudah running.

### Download gagal dengan error "cookies"
Pastikan file cookies sudah ada di path yang ditentukan di `COOKIES_PATH`.

### Transcription sangat lambat
- Gunakan GPU dengan set `USE_GPU=true`
- Gunakan model yang lebih kecil (tiny/base) di `WHISPER_MODEL`
- Reduce `WORKER_CONCURRENCY` untuk menghindari overload

### Callback tidak terkirim
- Pastikan `callback_url` dapat diakses dari container/server worker
- Periksa log worker untuk error detail
- Pastikan endpoint callback dapat menerima POST request dengan JSON body

## License

MIT License
