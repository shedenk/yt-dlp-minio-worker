# app.py
import os, uuid, redis, subprocess, json, hashlib
from typing import Any
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
r = redis.from_url(REDIS_URL, decode_responses=True)
ROLE = os.getenv("ROLE", "api")

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="yt-dlp API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


class DownloadReq(BaseModel):
    url: str
    filename: str | None = None
    format: str | None = None
    media: str | None = "video"  # "video", "audio", or "both" (video + audio)
    audio_format: str | None = "mp3"  # when media==audio or both
    transcribe: bool | None = True
    include_subs: bool | None = False
    sub_langs: str | None = "all"
    transcribe_lang: str | None = None
    transcribe_prompt: str | None = None
    download_option: int | None = None  # 1: video, 2: video+audio, 3: video+srt, 4: video+audio+srt


class ChannelCheckReq(BaseModel):
    channel_url: str
    media: str | None = "video"
    audio_format: str | None = "mp3"
    limit: int | None = 1
    track: bool | None = False  # set to True to enqueue videos for download
    wait: bool | None = False
    wait_timeout: int | None = 60
    transcribe: bool | None = True
    include_subs: bool | None = False
    sub_langs: str | None = "all"
    transcribe_lang: str | None = None
    transcribe_prompt: str | None = None
    download_option: int | None = None # 1: video, 2: video+audio, 3: video+srt, 4: video+audio+srt


def channel_key(url: str) -> str:
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return f"seen:channel:{h}"


def run_yt_dl_flat(channel_url: str):
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-json",
        channel_url,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except Exception:
            continue

@app.get("/health")
def health():
    return {"ok": True, "role": "api"}


@app.get("/service_status")
def service_status():
    """Return basic service health: Redis connectivity, queue length, MinIO status."""
    status: dict[str, Any] = {"ok": True, "role": ROLE}

    # Redis check
    try:
        pong = r.ping()
        qlen = r.llen("yt_queue")
        status["redis"] = {"ok": bool(pong), "queue_length": int(qlen)}
    except Exception as e:
        status["ok"] = False
        status["redis"] = {"ok": False, "error": str(e), "queue_length": None}

    # MinIO quick check if env present
    minio_info = {"configured": False, "ok": None}
    try:
        from minio import Minio
        MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT")
        MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY")
        MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY")
        MINIO_BUCKET = os.getenv("MINIO_BUCKET")
        MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

        if MINIO_ENDPOINT and MINIO_ACCESS_KEY and MINIO_SECRET_KEY and MINIO_BUCKET:
            minio_info["configured"] = True
            try:
                client = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY, secret_key=MINIO_SECRET_KEY, secure=MINIO_SECURE)
                exists = client.bucket_exists(MINIO_BUCKET)
                minio_info["ok"] = bool(exists)
            except Exception as e:
                minio_info["ok"] = False
                minio_info["error"] = str(e)
    except Exception:
        # minio package not installed or not configured
        pass

    status["minio"] = minio_info

    return status


@app.get("/jobs")
def list_jobs(limit: int = 20):
    """List up to `limit` job entries currently in the `yt_queue` (most recent first)."""
    try:
        ids = r.lrange("yt_queue", 0, max(0, int(limit) - 1))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"redis error: {e}")

    jobs = []
    for jid in ids:
        data = r.hgetall(f"job:{jid}") or {}
        # ensure job_id present
        entry = {"job_id": jid}
        entry.update(data)
        jobs.append(entry)

    return {"count": len(jobs), "jobs": jobs}

@app.post("/enqueue")
@limiter.limit("10/minute")
def enqueue(request: Request, req: DownloadReq):
    job_id = str(uuid.uuid4())

    # Map download_option if provided
    media = req.media or "video"
    transcribe = req.transcribe
    if req.download_option == 1:
        media, transcribe = "video", False
    elif req.download_option == 2:
        media, transcribe = "both", False
    elif req.download_option == 3:
        media, transcribe = "video", True
    elif req.download_option == 4:
        media, transcribe = "both", True

    r.hset(f"job:{job_id}", mapping={
        "status": "queued",
        "url": req.url,
        "filename": req.filename or job_id,
        "format": req.format or "",
        "media": media,
        "audio_format": req.audio_format or "wav",
        "transcribe": "true" if transcribe else "false",
        "include_subs": "true" if req.include_subs else "false",
        "sub_langs": req.sub_langs or "all",
        "transcribe_lang": req.transcribe_lang or "",
        "transcribe_prompt": req.transcribe_prompt or ""
    })
    r.lpush("yt_queue", job_id)

    return {"job_id": job_id, "status": "queued"}


@app.get("/status/{job_id}")
def status(job_id: str):
    data = r.hgetall(f"job:{job_id}")
    if not data:
        raise HTTPException(404, "job not found")
    return data


@app.post("/check_channel")
@limiter.limit("5/minute")
def check_channel(request: Request, req: ChannelCheckReq):
    import time

    
    seen = channel_key(req.channel_url)
    new_jobs = []
    new_urls = []

    for item in run_yt_dl_flat(req.channel_url):
        vid = item.get("id") or item.get("url")
        if not vid:
            continue

        # normalize video URL
        if len(vid) <= 32 and not vid.startswith("http"):
            video_url = f"https://www.youtube.com/watch?v={vid}"
        else:
            video_url = item.get("url") or vid

        # if tracking, check if already seen
        if req.track and r.sismember(seen, vid):
            continue

        upload_date = item.get("upload_date") or item.get("timestamp")
        title = item.get("title") or ""
        
        # add to results regardless of tracking
        new_urls.append({"url": video_url, "upload_date": upload_date, "title": title})

        # only enqueue if track=True
        if req.track:
            r.sadd(seen, vid)
            job_id = str(uuid.uuid4())
            
            # Map download_option if provided
            media = req.media or "video"
            transcribe = req.transcribe
            if req.download_option == 1:
                media, transcribe = "video", False
            elif req.download_option == 2:
                media, transcribe = "both", False
            elif req.download_option == 3:
                media, transcribe = "video", True
            elif req.download_option == 4:
                media, transcribe = "both", True

            mapping = {
                "status": "queued",
                "url": video_url,
                "filename": vid,
                "format": "",
                "media": media,
                "audio_format": req.audio_format or "wav",
                "transcribe": "true" if transcribe else "false",
                "include_subs": "true" if req.include_subs else "false",
                "sub_langs": req.sub_langs or "all",
                "transcribe_lang": req.transcribe_lang or "",
                "transcribe_prompt": req.transcribe_prompt or "",
                "upload_date": upload_date,
                "title": title,
            }
            # remove None values and stringify everything for Redis
            clean_mapping = {k: str(v) for k, v in mapping.items() if v is not None}
            r.hset(f"job:{job_id}", mapping=clean_mapping)
            r.lpush("yt_queue", job_id)
            new_jobs.append(job_id)

        # stop when we've collected the requested number of videos
        if req.limit and len(new_urls) >= int(req.limit):
            break

    result = {"new_count": len(new_urls), "job_ids": new_jobs, "video_urls": new_urls}

    if req.track and req.wait and new_jobs:
        # poll job statuses until done or timeout
        timeout = int(req.wait_timeout or 60)
        end = time.time() + timeout
        statuses = {}
        while time.time() < end:
            all_done = True
            for jid in new_jobs:
                data = r.hgetall(f"job:{jid}") or {}
                statuses[jid] = data
                st = data.get("status")
                if st not in ("done", "error"):
                    all_done = False
            if all_done:
                break
            time.sleep(1)

        # final fetch
        for jid in new_jobs:
            statuses[jid] = r.hgetall(f"job:{jid}") or {}

        result["job_statuses"] = statuses

    return result
