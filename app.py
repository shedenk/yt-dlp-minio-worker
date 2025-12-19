import os
import redis
from fastapi import FastAPI

ROLE = os.getenv("ROLE", "api")
REDIS_URL = os.getenv("REDIS_URL")

r = redis.from_url(REDIS_URL, decode_responses=True)
app = FastAPI(title="yt-dlp API")

# =========================
# MinIO (WORKER ONLY)
# =========================
minio_client = None
MINIO_PUBLIC_BASE_URL = os.getenv("MINIO_PUBLIC_BASE_URL", "").rstrip("/")

if ROLE == "worker":
    from minio import Minio

    MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT")
    MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY")
    MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY")
    MINIO_BUCKET = os.getenv("MINIO_BUCKET")
    MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

    # 🔥 VALIDASI KERAS (INI PENTING)
    missing = [
        k for k, v in {
            "MINIO_ENDPOINT": MINIO_ENDPOINT,
            "MINIO_ACCESS_KEY": MINIO_ACCESS_KEY,
            "MINIO_SECRET_KEY": MINIO_SECRET_KEY,
            "MINIO_BUCKET": MINIO_BUCKET,
        }.items() if not v
    ]

    if missing:
        raise RuntimeError(f"[FATAL] Missing MinIO env vars: {missing}")

    minio_client = Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE
    )
