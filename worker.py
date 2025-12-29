# worker.py
import os, time, subprocess, redis

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/data/downloads")
COOKIES_PATH = os.getenv("COOKIES_PATH", "/data/cookies/cookies.txt")
AUTO_DELETE_LOCAL = os.getenv("AUTO_DELETE_LOCAL", "true").lower() == "true"

r = redis.from_url(REDIS_URL, decode_responses=True)
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
# ensure cookies parent dir exists (mount-friendly)
try:
    os.makedirs(os.path.dirname(COOKIES_PATH), exist_ok=True)
except Exception:
    pass

print("▶ YT-DLP WORKER READY")

while True:
    job = r.brpop("yt_queue", timeout=5)
    if not job:
        continue

    job_id = job[1]
    data = r.hgetall(f"job:{job_id}")
    r.hset(f"job:{job_id}", "status", "processing")

    filename = data.get("filename", job_id)
    media = data.get("media", "video")
    audio_format = data.get("audio_format", "wav")
    outtmpl = f"{DOWNLOAD_DIR}/{filename}.%(ext)s"

    if media == "audio":
        local_file = f"{DOWNLOAD_DIR}/{filename}.{audio_format}"
        cmd = [
            "yt-dlp",
            "--js-runtimes", "node",
            "--force-ipv4",
            "--geo-bypass",
            "--no-progress",
            "-x",
            "--audio-format", audio_format,
            "-o", outtmpl,
            data["url"]
        ]
    else:
        local_file = f"{DOWNLOAD_DIR}/{filename}.mp4"
        cmd = [
            "yt-dlp",
            "--js-runtimes", "node",
            "--force-ipv4",
            "--geo-bypass",
            "--no-progress",
            "-f", data.get("format") or "bv*+ba/b",
            "--merge-output-format", "mp4",
            "-o", outtmpl,
            data["url"]
        ]

    if COOKIES_PATH and os.path.exists(COOKIES_PATH):
        cmd.insert(1, "--cookies")
        cmd.insert(2, COOKIES_PATH)

    try:
        subprocess.check_call(cmd)
        r.hset(f"job:{job_id}", mapping={
            "status": "done",
            "storage": "local",
            "filename": filename,
            "ext": os.path.splitext(local_file)[1].lstrip('.')
        })

        if AUTO_DELETE_LOCAL and os.path.exists(local_file):
            os.remove(local_file)

    except Exception as e:
        r.hset(f"job:{job_id}", mapping={
            "status": "error",
            "error": str(e)
        })

    time.sleep(1)
