# worker.py
import os, time, subprocess, redis
from redis.exceptions import ConnectionError as RedisConnectionError

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
    elif media == "both":
        # first download video (mp4), then extract audio to requested format with ffmpeg
        video_file = f"{DOWNLOAD_DIR}/{filename}.mp4"
        audio_file = f"{DOWNLOAD_DIR}/{filename}.{audio_format}"
        video_cmd = [
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
        cmd = None
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
        # insert cookies into the correct command (video_cmd for media==both)
        if media == "both":
            if 'video_cmd' in locals() and video_cmd is not None:
                video_cmd.insert(1, "--cookies")
                video_cmd.insert(2, COOKIES_PATH)
        else:
            if cmd is not None:
                cmd.insert(1, "--cookies")
                cmd.insert(2, COOKIES_PATH)

    try:
        # run commands depending on requested media
        # ensure Redis is available when we update status later
        if media == "both":
            subprocess.check_call(video_cmd)
            # extract audio using ffmpeg
            try:
                subprocess.check_call(["ffmpeg", "-y", "-i", video_file, audio_file])
            except Exception:
                # fallback: try yt-dlp audio extraction if ffmpeg fails
                subprocess.check_call([
                    "yt-dlp", "-x", "--audio-format", audio_format, "-o", outtmpl, data["url"]
                ])

            r.hset(f"job:{job_id}", mapping={
                "status": "done",
                "storage": "local",
                "video_file": video_file,
                "audio_file": audio_file
            })

            if AUTO_DELETE_LOCAL:
                if os.path.exists(video_file):
                    os.remove(video_file)
                if os.path.exists(audio_file):
                    os.remove(audio_file)
        else:
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
        # try to update job error status, reconnecting to Redis if needed
        try:
            r.hset(f"job:{job_id}", mapping={
                "status": "error",
                "error": str(e)
            })
        except RedisConnectionError:
            # attempt to recreate redis client once, then set error
            try:
                r = redis.from_url(REDIS_URL, decode_responses=True)
                r.hset(f"job:{job_id}", mapping={
                    "status": "error",
                    "error": str(e)
                })
            except Exception:
                # give up on updating status
                pass

    # small sleep to avoid tight loop; handle Redis connection issues around brpop
    time.sleep(1)
