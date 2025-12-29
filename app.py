# app.py
import os, uuid, redis
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
