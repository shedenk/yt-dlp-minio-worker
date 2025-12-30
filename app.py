# app.py
import os, uuid, redis, subprocess, json, hashlib
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
r = redis.from_url(REDIS_URL, decode_responses=True)

app = FastAPI(title="yt-dlp API")

class DownloadReq(BaseModel):
    url: str
    filename: str | None = None
    format: str | None = None
    media: str | None = "video"  # "video" or "audio"
    audio_format: str | None = "wav"  # when media==audio


class ChannelCheckReq(BaseModel):
    channel_url: str
    media: str | None = "video"
    audio_format: str | None = "wav"
    limit: int | None = 1


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

@app.post("/enqueue")
def enqueue(req: DownloadReq):
    job_id = str(uuid.uuid4())

    r.hset(f"job:{job_id}", mapping={
        "status": "queued",
        "url": req.url,
        "filename": req.filename or job_id,
        "format": req.format or "",
        "media": req.media or "video",
        "audio_format": req.audio_format or "wav"
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
def check_channel(req: ChannelCheckReq):
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

        if r.sismember(seen, vid):
            continue

        r.sadd(seen, vid)

        upload_date = item.get("upload_date") or item.get("timestamp")
        job_id = str(uuid.uuid4())
        mapping = {
            "status": "queued",
            "url": video_url,
            "filename": vid,
            "format": "",
            "media": req.media or "video",
            "audio_format": req.audio_format or "wav",
            "upload_date": upload_date,
        }
        # remove None values and stringify everything for Redis
        clean_mapping = {k: str(v) for k, v in mapping.items() if v is not None}
        r.hset(f"job:{job_id}", mapping=clean_mapping)
        r.lpush("yt_queue", job_id)
        new_jobs.append(job_id)
        new_urls.append({"url": video_url, "upload_date": upload_date})

        # stop when we've enqueued the requested number of new videos
        if req.limit and len(new_jobs) >= int(req.limit):
            break

    return {"new_count": len(new_jobs), "job_ids": new_jobs, "video_urls": new_urls}
