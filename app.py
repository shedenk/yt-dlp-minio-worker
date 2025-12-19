import os, time, json, subprocess, uuid
import redis
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

ROLE = os.getenv("ROLE", "api")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

r = redis.from_url(REDIS_URL, decode_responses=True)
app = FastAPI(title="yt-dlp API")

DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# =========================
# MinIO (WORKER ONLY)
# =========================
MINIO_STRICT = os.getenv("MINIO_STRICT", "true").lower() == "true"
MINIO_PUBLIC_BASE_URL = os.getenv("MINIO_PUBLIC_BASE_URL", "").rstrip("/")
minio_client = None
MINIO_BUCKET = None

if ROLE == "worker":
    try:
        from minio import Minio

        MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT")
        MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY")
        MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY")
        MINIO_BUCKET = os.getenv("MINIO_BUCKET")
        MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

        missing = [
            k for k, v in {
                "MINIO_ENDPOINT": MINIO_ENDPOINT,
                "MINIO_ACCESS_KEY": MINIO_ACCESS_KEY,
                "MINIO_SECRET_KEY": MINIO_SECRET_KEY,
                "MINIO_BUCKET": MINIO_BUCKET,
            }.items() if not v
        ]

        if missing:
            msg = f"MinIO ENV incomplete: {missing}"
            if MINIO_STRICT:
                raise RuntimeError(msg)
            else:
                print(f"[WARN] {msg}, upload disabled")
        else:
            minio_client = Minio(
                MINIO_ENDPOINT,
                access_key=MINIO_ACCESS_KEY,
                secret_key=MINIO_SECRET_KEY,
                secure=MINIO_SECURE
            )

    except Exception as e:
        if MINIO_STRICT:
            raise
        print(f"[WARN] MinIO disabled: {e}")

# =========================
# API
# =========================
class DownloadReq(BaseModel):
    url: str
    format: str = "bestvideo+bestaudio/best"
    filename: str | None = None

@app.get("/health")
def health():
    return {"ok": True, "role": ROLE}

@app.post("/enqueue")
def enqueue(req: DownloadReq):
    job_id = str(uuid.uuid4())
    r.hset(f"job:{job_id}", mapping={
        "status": "queued",
        "url": req.url,
        "format": req.format,
        "filename": req.filename or job_id
    })
    r.lpush("yt_queue", job_id)
    return {"job_id": job_id, "status": "queued"}

@app.get("/status/{job_id}")
def status(job_id: str):
    data = r.hgetall(f"job:{job_id}")
    if not data:
        raise HTTPException(404, "job not found")
    return data

# =========================
# WORKER
# =========================
def upload_to_minio(local_path, object_name):
    minio_client.fput_object(
        MINIO_BUCKET,
        object_name,
        local_path,
        content_type="video/mp4"
    )
    return f"{MINIO_PUBLIC_BASE_URL}/{object_name}"

def worker():
    print("▶ YT-DLP WORKER RUNNING")
    while True:
        job = r.brpop("yt_queue", timeout=5)
        if not job:
            continue

        job_id = job[1]
        data = r.hgetall(f"job:{job_id}")
        r.hset(f"job:{job_id}", "status", "processing")

        filename = data.get("filename", job_id)
        outtmpl = f"{DOWNLOAD_DIR}/{filename}.%(ext)s"
        local_file = f"{DOWNLOAD_DIR}/{filename}.mp4"

        try:
            subprocess.run([
                "yt-dlp",
                "-f", data.get("format"),
                "--merge-output-format", "mp4",
                "-o", outtmpl,
                data["url"]
            ], check=True)

            public_url = ""
            if minio_client:
                public_url = upload_to_minio(local_file, f"{filename}.mp4")

            r.hset(f"job:{job_id}", mapping={
                "status": "done",
                "public_url": public_url,
                "storage": "minio" if minio_client else "local"
            })

            if os.getenv("AUTO_DELETE_LOCAL", "true").lower() == "true":
                if os.path.exists(local_file):
                    os.remove(local_file)

        except Exception as e:
            r.hset(f"job:{job_id}", mapping={
                "status": "error",
                "error": str(e)
            })

        time.sleep(1)

if ROLE == "worker":
    worker()
