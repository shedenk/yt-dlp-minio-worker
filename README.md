# yt-dlp-minio-worker

This repository provides a FastAPI service and worker for downloading YouTube videos using `yt-dlp`, optionally uploading to MinIO, and performing AI transcription.

## Features

- **Download Options**: Choose between Video only, Video + Audio, Video + SRT, or Video + Audio + SRT.
- **Progress Tracking**: Real-time progress percentage in job status and worker logs.
- **Callback URL**: Webhook notification once the job is finished.
- **Transcription**: Powered by **Faster-Whisper** with progress awareness.
- **Reliability**: Retry mechanism with exponential backoff and timeout protection.
- **MinIO Integration**: Automatically uploads results to MinIO and returns public links.

## API Reference

### **POST /enqueue**

- Description: Enqueue a download job.
- Request JSON:

```json
{
  "url": "https://www.youtube.com/watch?v=...",
  "download_option": 4,
  "callback_url": "https://your-api.com/callback",
  "db_id": "your-internal-database-id",
  "transcribe_lang": "id",
  "include_subs": false
}
```

- **Parameters**:
  - `download_option`: 
    - `1`: Video only
    - `2`: Video + Audio
    - `3`: Video + SRT
    - `4`: Video + Audio + SRT
  - `callback_url`: (Optional) URL to receive a POST request when the job is done.
  - `transcribe_lang`: ISO code for language (e.g., `id`, `en`).
  - `include_subs`: Set to `true` to download YouTube's original subtitles.

### **GET /status/{job_id}**

- **Response Example (Done)**:
```json
{
  "status": "done",
  "progress": "100",
  "video_duration": 212,
  "audio_duration": 212,
  "video_quality": "1080p",
  "video_fps": "30",
  "audio_quality": "128kbps",
  "db_id": "your-internal-database-id",
  "video_file": "https://minio.com/bucket/video.mp4",
  "audio_file": "https://minio.com/bucket/audio.mp3",
  "transcript_file": "https://minio.com/bucket/transcript.srt"
}
```

### **Callback Webhook**

If a `callback_url` is provided, the worker will send a POST request upon completion (success or error). The body will contain the final job data, including `job_id` and excluding the `heartbeat` field.

**Callback Payload Example**:
```json
{
  "job_id": "abc-123",
  "db_id": "your-internal-database-id",
  "status": "done",
  "progress": "100",
  "video_duration": 212,
  "audio_duration": 212,
  "video_quality": "1080p",
  "video_fps": "30",
  "audio_quality": "128kbps",
  "video_file": "https://minio.com/bucket/video.mp4",
  "audio_file": "https://minio.com/bucket/audio.mp3",
  "transcript_file": "https://minio.com/bucket/transcript.srt"
}
```

---

## Technical Structure

- `app.py`: FastAPI endpoints.
- `worker.py`: Background worker consuming the Redis queue.
- `Dockerfile` & `docker-compose.yaml`: Containerized deployment.

## Installation

1. Copy `.env.example` to `.env` and configure your credentials.
2. Run `docker-compose up --build`.

---
For more details, see `app.py` and `worker.py`.

{
  "job_id": "9e2a1a19-6d5b-40e6-bbdf-67df292ab753",
  "db_id": "154",
  "status": "done",
  "progress": "100",
  "video_duration": "394",
  "audio_duration": "394",
  "video_quality": "4k",
  "video_fps": "60",
  "audio_quality": "95kbps",
  "video_file": "http://minio:9000/videos/9e2a1a19-6d5b-40e6-bbdf-67df292ab753.mp4",
  "audio_file": "http://minio:9000/videos/9e2a1a19-6d5b-40e6-bbdf-67df292ab753.mp3",
  "transcript_file": "http://minio:9000/videos/9e2a1a19-6d5b-40e6-bbdf-67df292ab753.srt",
  "subtitles_file": "{}",
  "public_video": "http://minio:9000/videos/9e2a1a19-6d5b-40e6-bbdf-67df292ab753.mp4",
  "public_audio": "http://minio:9000/videos/9e2a1a19-6d5b-40e6-bbdf-67df292ab753.mp3",
  "public_transcript": "http://minio:9000/videos/9e2a1a19-6d5b-40e6-bbdf-67df292ab753.srt",
  "public_subtitles": "{}",
  "url": "https://www.youtube.com/watch?v=tf0MLZJGUbQ",
  "callback_url": "http://n8n:5678/webhook/61b2d375-c9b9-4e3f-baae-1ff67809eff9",
  "transcribe": "true",
  "transcribe_lang": "id",
  "storage": "minio"
}