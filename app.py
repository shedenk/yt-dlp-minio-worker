# app.py
import os, uuid, redis, subprocess, json, hashlib
from typing import Any
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

REDIS_URL = os.getenv("REDIS_URL", "redis://yt-redis:6379/0")
print(f"[INFO] Connecting to Redis...")

def get_redis_client(url: str):
    # Mask password for logging
    masked_url = url
    pwd_len = 0
    client = None
    if "@" in url:
        try:
            auth_part, host_part = url.split("://")[1].split("@")
            if ":" in auth_part:
                _, pwd = auth_part.split(":", 1)
                pwd_len = len(pwd)
                masked_url = f"redis://:****@{host_part}"
            else:
                pwd_len = len(auth_part)
                masked_url = f"redis://:****@{host_part}"
        except Exception:
            masked_url = "redis://****@..."
    
    print(f"[DEBUG] Connecting to Redis at {masked_url} (password length: {pwd_len})")
    
    try:
        client = redis.from_url(url, decode_responses=True)
        # Force immediate connection check
        client.ping()
        print("[INFO] Redis connection successful")
        return client
    except redis.exceptions.AuthenticationError:
        print("[CRITICAL] Redis Authentication failed! The provided password is incorrect.")
        print("[CRITICAL] Application will now exit to prevent inconsistent state.")
        import sys
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Redis connection failed: {e}")
        # For other errors (like connection refused), we might want to wait/retry, 
        # but for now, let's just log it.
        return client

r = get_redis_client(REDIS_URL)

ROLE = os.getenv("ROLE", "api")
COOKIES_PATH = os.getenv("COOKIES_PATH", "/data/cookies/cookies.txt")

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="yt-dlp API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


class DownloadReq(BaseModel):
    url: str
    video: bool = True
    audio: bool = False
    transcribe: bool = False
    callback_url: str | None = None
    db_id: str | None = None


class ChannelCheckReq(BaseModel):
    channel_url: str
    limit: int | None = 1
    track: bool | None = False  # set to True to enqueue videos for download


def channel_key(url: str) -> str:
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return f"seen:channel:{h}"


def run_yt_dl_flat(channel_url: str):
    if COOKIES_PATH:
        if os.path.exists(COOKIES_PATH):
            print(f"[INFO] Using cookies from {COOKIES_PATH} (size: {os.path.getsize(COOKIES_PATH)} bytes)")
        else:
            print(f"[WARN] Cookies file NOT FOUND at {COOKIES_PATH}")

    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-json",
        "--socket-timeout", "15",
        "--",
        channel_url,
    ]
    if COOKIES_PATH and os.path.exists(COOKIES_PATH):
        cmd.insert(1, "--cookies")
        cmd.insert(2, COOKIES_PATH)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except Exception:
            continue

def check_video_has_subtitles(url: str) -> bool:
    cmd = [
        "yt-dlp",
        "--dump-json",
        "--no-playlist",
        "--socket-timeout", "15",
        "--",
        url,
    ]
    if COOKIES_PATH and os.path.exists(COOKIES_PATH):
        cmd.insert(1, "--cookies")
        cmd.insert(2, COOKIES_PATH)
        
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, _ = proc.communicate()
        if proc.returncode == 0 and stdout:
            info = json.loads(stdout)
            return bool(info.get("subtitles") or info.get("automatic_captions"))
    except Exception as e:
        print(f"[WARN] Error checking subtitles for {url}: {e}")
    return False

def download_subtitle(video_url: str, video_id: str) -> str:
    """Download Indonesian subtitle for a video and upload to MinIO.
    
    Returns:
        MinIO URL of the subtitle file, or empty string if failed
    """
    import tempfile
    import shutil
    
    # Create temp directory for download
    temp_dir = tempfile.mkdtemp()
    
    try:
        # Download only Indonesian subtitle
        output_template = f"{temp_dir}/{video_id}.%(ext)s"
        cmd = [
            "yt-dlp",
            "--write-subs",
            "--write-auto-subs",
            "--sub-format", "srt",
            "--sub-langs", "id",
            "--skip-download",  # Don't download video, only subtitle
            "--socket-timeout", "15",
            "-o", output_template,
            "--",
            video_url
        ]
        
        if COOKIES_PATH and os.path.exists(COOKIES_PATH):
            cmd.insert(1, "--cookies")
            cmd.insert(2, COOKIES_PATH)
        
        # Run download with timeout
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            stdout, stderr = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            print(f"[WARN] Subtitle download timeout for {video_id}")
            return ""
        
        if proc.returncode != 0:
            print(f"[WARN] Subtitle download failed for {video_id}")
            return ""
        
        # Find the downloaded subtitle file
        subtitle_file = None
        for f in os.listdir(temp_dir):
            if f.endswith(".srt") and video_id in f:
                subtitle_file = f
                break
        
        if not subtitle_file:
            print(f"[INFO] No Indonesian subtitle found for {video_id}")
            return ""
        
        # Rename to standard format
        old_path = f"{temp_dir}/{subtitle_file}"
        new_path = f"{temp_dir}/{video_id}.srt"
        if old_path != new_path:
            os.rename(old_path, new_path)
        
        # Upload to MinIO
        try:
            from minio import Minio
            MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT")
            MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY")
            MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY")
            MINIO_BUCKET = os.getenv("MINIO_BUCKET")
            MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"
            MINIO_PUBLIC_BASE_URL = os.getenv("MINIO_PUBLIC_BASE_URL", "").rstrip('/')
            
            if MINIO_ENDPOINT and MINIO_ACCESS_KEY and MINIO_SECRET_KEY and MINIO_BUCKET:
                client = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY, 
                              secret_key=MINIO_SECRET_KEY, secure=MINIO_SECURE)
                
                client.fput_object(MINIO_BUCKET, f"{video_id}.srt", new_path)
                public_url = f"{MINIO_PUBLIC_BASE_URL}/{video_id}.srt"
                print(f"[INFO] Uploaded subtitle to MinIO: {public_url}")
                return public_url
            else:
                print(f"[WARN] MinIO not configured, subtitle not uploaded")
                return ""
        except Exception as e:
            print(f"[ERROR] MinIO upload failed for {video_id}: {e}")
            return ""
            
    except Exception as e:
        print(f"[ERROR] Subtitle download error for {video_id}: {e}")
        return ""
    finally:
        # Cleanup temp directory
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass


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
    # Reject playlist URLs and Shorts to prevent worker overload
    if "list=" in req.url or "/playlist" in req.url:
        raise HTTPException(status_code=400, detail="Playlist URLs are not allowed. Please provide a single video URL.")

    job_id = str(uuid.uuid4())

    # Determine media type based on video/audio flags
    media = "video"
    if req.video and req.audio:
        media = "both"
    elif req.audio:
        media = "audio"
    elif req.video:
        media = "video"

    r.hset(f"job:{job_id}", mapping={
        "status": "queued",
        "url": req.url,
        "filename": job_id,
        "format": "",
        "media": media,
        "audio_format": "mp3" if req.audio else "wav",
        "transcribe": "true" if req.transcribe else "false",
        "include_subs": "false",  # Don't download YouTube subtitles for /enqueue
        "sub_langs": "",
        "transcribe_lang": "id" if req.transcribe else "",
        "transcribe_prompt": "",
        "callback_url": req.callback_url or "",
        "db_id": req.db_id or ""
    })
    r.lpush("yt_queue", job_id)

    return {"job_id": job_id, "status": "queued"}


@app.get("/status/{job_id}")
def get_status(job_id: str):
    data = r.hgetall(f"job:{job_id}")
    if not data:
        raise HTTPException(404, "job not found")
    
    # Filter out internal/redundant fields (as per user request)
    # Note: duration might be useful so I'll keep it if it's there
    for field in ["heartbeat", "storage", "public_video", "public_audio", "public_transcript", "public_subtitles", "subtitles_file"]:
        if field in data:
            del data[field]
            
    return data


@app.post("/check_channel")
@limiter.limit("5/minute")
def check_channel(request: Request, req: ChannelCheckReq):
    
    seen = channel_key(req.channel_url)
    new_urls = []

    for item in run_yt_dl_flat(req.channel_url):
        vid = item.get("id") or item.get("url")
        if not vid:
            continue

        # Filter: Skip Live, Upcoming, Shorts, and Short Videos (< 15 mins)
        if item.get("live_status") in ("is_live", "is_upcoming"):
            continue
            
        if "/shorts/" in (item.get("url") or ""):
            continue
            
        # Treat None duration as 0 (skip if unknown/live)
        duration = item.get("duration") or 0
        if duration < 900:
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

        # Check if video has subtitles
        has_subtitles = check_video_has_subtitles(video_url)
        
        # Download subtitle if available
        subtitle_url = ""
        if has_subtitles:
            subtitle_url = download_subtitle(video_url, vid)
        
        # add to results with subtitle URL
        new_urls.append({
            "url": video_url, 
            "upload_date": upload_date, 
            "title": title, 
            "has_subtitles": has_subtitles, 
            "subtitle_url": subtitle_url,
            "duration": duration
        })

        # only enqueue if track=True
        if req.track:
            r.sadd(seen, vid)

        # stop when we've collected the requested number of videos
        if req.limit and len(new_urls) >= int(req.limit):
            break

    return {"new_count": len(new_urls), "video_urls": new_urls}
