# yt-dlp-minio-worker

This repository provides a small FastAPI service and worker for downloading
YouTube videos using `yt-dlp`, optionally uploading to MinIO, and extracting
audio (e.g. WAV). It also includes a helper to check channels for new
uploads and enqueue them.
**Structure**

- `app.py`: FastAPI application exposing `/enqueue`, `/status/{job_id}`, and `/check_channel` endpoints.
- `worker.py`: background worker with **retry mechanism**, **multiprocessing concurrency**, and **timeout protection** that pops jobs from Redis (`yt_queue`) and runs `yt-dlp` (and `ffmpeg` for audio extraction).
- `check_channel.py`: standalone script to scan a YouTube channel (`--flat-playlist --dump-json`) and enqueue unseen videos.
- `cleanup.py`: periodic cleanup of old files in the download directory.
- `Dockerfile`, `docker-compose.yaml`: container configuration (includes `ffmpeg` and `nodejs` for yt-dlp JS runtime).
- `requirements.txt`: Python dependencies.

**How it works (quick)**

- POST `/enqueue` → creates a Redis job entry and pushes its `job_id` to `yt_queue`.
- `worker.py` (ROLE=worker) consumes `yt_queue`, downloads video and/or extracts audio, then updates job status in Redis.
- POST `/check_channel` → runs `yt-dlp --flat-playlist --dump-json` for the given channel, records seen videos in Redis, enqueues new ones, and returns the new job ids and URLs.

**Worker Features**

- **Retry Mechanism**: Automatically retries failed jobs up to 3 times with exponential backoff (60s, 120s, 240s)
- **Concurrency**: Process multiple jobs in parallel (default: 3 workers) using multiprocessing
- **Timeout Protection**: Automatically kills jobs that exceed timeout (default: 1 hour) and retries them
- **Progress Tracking**: Tracks retry attempts and errors in Redis for debugging
- **Graceful Shutdown**: Handles SIGTERM/SIGINT properly to finish ongoing downloads

**API Reference**

**POST /enqueue**

- Description: Enqueue a single download job.
- Request JSON:

```json
{
  "url": "https://www.youtube.com/watch?v=...",
  "filename": "optional-filename",
  "format": "optional-yt-dlp-format",
  "media": "video",
  "audio_format": "wav"
}
```

- Notes:

  - `media` values: `video` (default), `audio`, `both`.
  - `audio_format`: format used when extracting audio (e.g. `wav`, `mp3`).

- Response (HTTP 200):

```json
{
  "job_id": "<uuid>",
  "status": "queued"
}
```

**GET /status/{job_id}**

- Description: Retrieve job status and metadata.
- Response examples:

- Video job finished (media=video):

```json
{
  "status": "done",
  "storage": "local",
  "filename": "<name>",
  "ext": "mp4"
}
```

- Audio job finished (media=audio):

```json
{
  "status": "done",
  "storage": "local",
  "filename": "<name>",
  "ext": "wav"
}
```

- Both (media=both) finished:

```json
{
  "status": "done",
  "storage": "local",
  "video_file": "/data/downloads/<name>.mp4",
  "audio_file": "/data/downloads/<name>.wav"
}
```

**POST /check_channel**

- Description: Scan a YouTube channel for new uploads and enqueue them.
- Request JSON:

```json
{
  "channel_url": "https://www.youtube.com/channel/UC.../videos",
  "media": "video",
  "audio_format": "wav"
}
```

- Response (HTTP 200):

```json
{
  "new_count": 2,
  "job_ids": ["<uuid1>", "<uuid2>"],
  "video_urls": [
    "https://www.youtube.com/watch?v=...",
    "https://www.youtube.com/watch?v=..."
  ]
}
```

**Examples**

- Enqueue both video + wav extraction:

```bash
curl -X POST http://localhost:8080/enqueue \
	-H "Content-Type: application/json" \
	-d '{"url":"https://www.youtube.com/watch?v=...","media":"both","audio_format":"wav"}'
```

- Check a channel and get new video URLs:

```bash
curl -X POST http://localhost:8080/check_channel \
	-H "Content-Type: application/json" \
	-d '{"channel_url":"https://www.youtube.com/channel/UC.../videos","media":"video"}'
```

**Running locally with Docker**

1. Build and bring up services:

```bash
docker compose up -d --build
```

2. Run channel checker from inside container (optional):

```bash
docker compose run --rm yt-dlp-api python check_channel.py "https://www.youtube.com/channel/UC.../videos"
```

**Configuration**

The worker can be configured via environment variables in `docker-compose.yaml`:

- `WORKER_CONCURRENCY`: Number of parallel workers (default: 3)
- `MAX_RETRIES`: Maximum retry attempts per job (default: 3)
- `JOB_TIMEOUT`: Job timeout in seconds (default: 3600 = 1 hour)
- `RETRY_BACKOFF_BASE`: Base delay for exponential backoff (default: 60 seconds)
- `AUTO_DELETE_LOCAL`: Delete local files after upload (default: true)

Example for high-volume workloads:
```yaml
environment:
  WORKER_CONCURRENCY: "5"
  MAX_RETRIES: "5"
  JOB_TIMEOUT: "7200"  # 2 hours for very large files
```

**Notes & Tips**

- Ensure `DOWNLOAD_DIR` in `docker-compose.yaml` is mounted to a persistent volume (default `/data/downloads`).
- `ffmpeg` is required for audio extraction; the Dockerfile installs it.
- `AUTO_DELETE_LOCAL` controls whether worker removes local copies after processing.

---

For more details see `app.py`, `worker.py`, and `check_channel.py`.

**Cookies Setup**

To use cookies for authenticated downloads, copy your `cookies.txt` file to the cookies volume. The file will be automatically mounted at `/data/cookies/cookies.txt` inside the container.