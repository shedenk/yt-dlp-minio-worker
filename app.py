from minio import Minio
from minio.error import S3Error
import os, json, uuid, subprocess, time, redis

ROLE = os.getenv("ROLE")
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/downloads")
REDIS_URL = os.getenv("REDIS_URL")

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY")
MINIO_BUCKET = os.getenv("MINIO_BUCKET")
MINIO_PUBLIC_BASE_URL = os.getenv("MINIO_PUBLIC_BASE_URL")
AUTO_DELETE_LOCAL = os.getenv("AUTO_DELETE_LOCAL", "true").lower() == "true"



r = redis.from_url(REDIS_URL, decode_responses=True)

minio_client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False
)

if not minio_client.bucket_exists(MINIO_BUCKET):
    minio_client.make_bucket(MINIO_BUCKET)

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

        filename = data.get("filename") or job_id
        local_file = f"{DOWNLOAD_DIR}/{filename}.mp4"
        outtmpl = f"{DOWNLOAD_DIR}/{filename}.%(ext)s"

        cmd = [
            "yt-dlp",
            "-f", data.get("format", "bestvideo+bestaudio/best"),
            "--merge-output-format", "mp4",
            "-o", outtmpl,
            data["url"]
        ]

        try:
            # 1. Download
            subprocess.run(cmd, check=True)

            # 2. Upload to MinIO
            object_name = f"{filename}.mp4"
            public_url = upload_to_minio(local_file, object_name)

            # 3. Save job result
            r.hset(f"job:{job_id}", mapping={
                "status": "done",
                "public_url": public_url,
                "object_name": object_name
            })

            # 4. AUTO DELETE LOCAL FILE
            if AUTO_DELETE_LOCAL:
                deleted = safe_delete(local_file)
                r.hset(
                    f"job:{job_id}",
                    "local_deleted",
                    "true" if deleted else "false"
                )

        except Exception as e:
            r.hset(f"job:{job_id}", mapping={
                "status": "error",
                "error": str(e),
                "local_file": local_file
            })

        time.sleep(1)

def safe_delete(path):
    try:
        if os.path.exists(path):
            os.remove(path)
            return True
    except Exception as e:
        print(f"[WARN] Failed delete {path}: {e}")
    return False

if ROLE == "worker":
    worker()
