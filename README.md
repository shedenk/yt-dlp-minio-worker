# yt-dlp-minio-worker

This repository provides a small FastAPI service and worker for downloading
YouTube videos using `yt-dlp`, optionally uploading to MinIO, and extracting
audio (e.g. WAV). It also includes a helper to check channels for new
uploads and enqueue them.
**Structure**

- `app.py`: FastAPI application exposing `/enqueue`, `/status/{job_id}`, and `/check_channel` endpoints.
- `worker.py`: background worker with **retry mechanism**, **multiprocessing concurrency**, and **timeout protection** that pops jobs from Redis (`yt_queue`) and runs `yt-dlp`, `ffmpeg` (for audio extraction), and **OpenAI Whisper** (for transcription).
- `check_channel.py`: standalone script to scan a YouTube channel (`--flat-playlist --dump-json`) and enqueue unseen videos.
- `cleanup.py`: periodic cleanup of old files in the download directory.
- `Dockerfile`, `docker-compose.yaml`: container configuration (includes `ffmpeg` and `nodejs` for yt-dlp JS runtime).
- `requirements.txt`: Python dependencies.

**How it works (quick)**

- POST `/enqueue` → creates a Redis job entry and pushes its `job_id` to `yt_queue`.
- `worker.py` (ROLE=worker) consumes `yt_queue`, downloads video and/or extracts audio, performs transcription if requested, then updates job status in Redis.
- POST `/check_channel` → runs `yt-dlp --flat-playlist --dump-json` for the given channel, records seen videos in Redis, enqueues new ones, and returns the new job ids and URLs.

**Worker Features**

- **Retry Mechanism**: Automatically retries failed jobs up to 3 times with exponential backoff (60s, 120s, 240s)
- **Concurrency**: Process multiple jobs in parallel (default: 3 workers) using multiprocessing
- **Timeout Protection**: Automatically kills jobs that exceed timeout (default: 1 hour) and retries them
- **Progress Tracking**: Tracks retry attempts and errors in Redis for debugging
- **Graceful Shutdown**: Handles SIGTERM/SIGINT properly to finish ongoing downloads

**API Reference**

### **POST /enqueue**

- Description: Enqueue a single download job.
- Request JSON:

```json
{
  "url": "https://www.youtube.com/watch?v=...",
  "filename": "optional-filename",
  "media": "video",
  "audio_format": "mp3",
  "transcribe": true,
  "transcribe_lang": "id",
  "transcribe_prompt": "Optional context for Whisper",
  "include_subs": false,
  "sub_langs": "all"
}
```

- **Parameters**:
  - `media`: `video` (default), `audio`, or `both` (video + audio).
  - `audio_format`: output format for audio (default: `mp3`).
  - `transcribe`: set to `true` (default) for AI transcription using **Faster-Whisper**.
  - `transcribe_lang`: ISO code for language (e.g., `id`, `en`).
  - `transcribe_prompt`: optional text to guide Whisper's formatting/style.
  - `include_subs`: set to `true` to download YouTube's original subtitles.

- Response (HTTP 200):
```json
{
  "job_id": "<uuid>",
  "status": "queued"
}
```

### **GET /status/{job_id}**

- Description: Retrieve job status and results.
- **Statuses**: `queued`, `processing`, `transcribing (X%)`, `done`, `error`.

- **Response Examples**:

- **Processing / Transcribing**:
```json
{
  "status": "transcribing (45.2%)",
  "heartbeat": 1704170000,
  "url": "https://...",
  "media": "video"
}
```

- **Done (media=both)**:
```json
{
  "status": "done",
  "storage": "minio",
  "public_video": "https://.../video.mp4",
  "public_audio": "https://.../audio.mp3",
  "public_transcript": "https://.../transcript.srt",
  "transcript_file": "/data/downloads/name.srt",
  "heartbeat": 1704170050
}
```

### **POST /check_channel**

- Description: Scan a YouTube channel for new uploads. Supports same parameters as `/enqueue` plus:
  - `limit`: max items to check (default: 1).
  - `track`: if `true`, enqueues new videos and remembers them.
  - `wait`: if `true`, waits for all enqueued jobs to finish.

**CLI Tools**

You can also run the channel checker via command line:
```bash
python check_channel.py "https://www.youtube.com/..." --track --media both --transcribe --lang id
```

**Configuration**

Edit `docker-compose.yaml` to adjust performance vs. resources:

- `WORKER_CONCURRENCY`: Parallel jobs (default: **1** recommended for stable RAM).
- `JOB_TIMEOUT`: Max processing time (default: **14400** = 4 hours).
- `WHISPER_MODEL`: Faster-Whisper model (default: `base`).
- `USE_GPU`: Set to `true` if you have NVIDIA GPU and drivers configured.

**Cookies Setup**

To use cookies for authenticated downloads, copy your `cookies.txt` file to the cookies volume. The file will be automatically mounted at `/data/cookies/cookies.txt` inside the container.

---

For more details see `app.py`, `worker.py`, and `check_channel.py`.