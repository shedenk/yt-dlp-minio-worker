# worker.py
import os, time, subprocess, redis, signal, multiprocessing, json
from redis.exceptions import ConnectionError as RedisConnectionError
from typing import Optional
from contextlib import contextmanager

minio_client = None
MINIO_BUCKET = None
MINIO_PUBLIC_BASE_URL = os.getenv("MINIO_PUBLIC_BASE_URL", "").rstrip('/')
try:
    from minio import Minio
    MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT")
    MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY")
    MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY")
    MINIO_BUCKET = os.getenv("MINIO_BUCKET")
    MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

    if MINIO_ENDPOINT and MINIO_ACCESS_KEY and MINIO_SECRET_KEY and MINIO_BUCKET:
        minio_client = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=MINIO_SECURE,
        )
        print("[INFO] MinIO client initialized")
    else:
        print("[INFO] MinIO not configured; uploads disabled")
except Exception as e:
    minio_client = None
    print(f"[WARN] MinIO client init failed: {e}")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/data/downloads")
COOKIES_PATH = os.getenv("COOKIES_PATH", "/data/cookies/cookies.txt")
AUTO_DELETE_LOCAL = os.getenv("AUTO_DELETE_LOCAL", "true").lower() == "true"

# New configuration for retry and concurrency
WORKER_CONCURRENCY = int(os.getenv("WORKER_CONCURRENCY", "3"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
JOB_TIMEOUT = int(os.getenv("JOB_TIMEOUT", "3600"))  # 1 hour default
RETRY_BACKOFF_BASE = int(os.getenv("RETRY_BACKOFF_BASE", "60"))  # 60 seconds

r = redis.from_url(REDIS_URL, decode_responses=True)
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
# ensure cookies parent dir exists (mount-friendly)
try:
    os.makedirs(os.path.dirname(COOKIES_PATH), exist_ok=True)
except Exception:
    pass

# Log cookies file status
if os.path.exists(COOKIES_PATH):
    print(f"[INFO] Cookies file found at {COOKIES_PATH}")
else:
    print(f"[WARN] Cookies file not found at {COOKIES_PATH} - authenticated downloads may fail")


class TimeoutException(Exception):
    """Raised when a job exceeds its timeout"""
    pass


@contextmanager
def timeout_handler(seconds: int):
    """Context manager for timeout protection using signal.alarm (Unix only)"""
    def _timeout_handler(signum, frame):
        raise TimeoutException(f"Job exceeded timeout of {seconds} seconds")
    
    # Set the signal handler and alarm
    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(seconds)
    
    try:
        yield
    finally:
        # Disable the alarm and restore old handler
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def get_redis_connection():
    """Get a fresh Redis connection (for multiprocessing safety)"""
    return redis.from_url(REDIS_URL, decode_responses=True)


def process_single_job(job_id: str) -> bool:
    """
    Process a single job with retry logic and timeout protection.
    Returns True if successful, False otherwise.
    """
    r_local = get_redis_connection()
    
    for attempt in range(MAX_RETRIES):
        try:
            # Update retry count in Redis
            r_local.hset(f"job:{job_id}", "retry_count", attempt)
            r_local.hset(f"job:{job_id}", "status", "processing")
            
            print(f"[INFO] Processing job {job_id} (attempt {attempt + 1}/{MAX_RETRIES})")
            
            # Process job with timeout protection
            with timeout_handler(JOB_TIMEOUT):
                success = _execute_download(job_id, r_local)
                
            if success:
                print(f"[SUCCESS] Job {job_id} completed successfully")
                return True
                
        except TimeoutException as e:
            error_msg = f"Timeout after {JOB_TIMEOUT}s (attempt {attempt + 1}/{MAX_RETRIES})"
            print(f"[WARN] Job {job_id}: {error_msg}")
            r_local.hset(f"job:{job_id}", "last_error", str(e))
            
            if attempt < MAX_RETRIES - 1:
                # Exponential backoff: 60s, 120s, 240s, etc.
                backoff = min(300, RETRY_BACKOFF_BASE * (2 ** attempt))
                print(f"[INFO] Retrying job {job_id} in {backoff}s...")
                time.sleep(backoff)
            else:
                # Final attempt failed
                r_local.hset(f"job:{job_id}", mapping={
                    "status": "error",
                    "error": f"Failed after {MAX_RETRIES} attempts: {error_msg}"
                })
                return False
                
        except Exception as e:
            error_msg = str(e)
            print(f"[ERROR] Job {job_id}: {error_msg} (attempt {attempt + 1}/{MAX_RETRIES})")
            r_local.hset(f"job:{job_id}", "last_error", error_msg)
            
            if attempt < MAX_RETRIES - 1:
                backoff = min(300, RETRY_BACKOFF_BASE * (2 ** attempt))
                print(f"[INFO] Retrying job {job_id} in {backoff}s...")
                time.sleep(backoff)
            else:
                # Final attempt failed
                try:
                    r_local.hset(f"job:{job_id}", mapping={
                        "status": "error",
                        "error": f"Failed after {MAX_RETRIES} attempts: {error_msg}"
                    })
                except Exception:
                    pass
                return False
    
    return False


def _execute_download(job_id: str, r_local: redis.Redis) -> bool:
    """
    Execute the actual download logic for a job.
    Returns True if successful, False otherwise.
    """
    data = r_local.hgetall(f"job:{job_id}")
    if not data:
        print(f"[ERROR] Job {job_id} not found in Redis")
        return False
    print(f"[DEBUG] Job data: {data}")
    
    filename = data.get("filename", job_id)
    media = data.get("media", "video")
    audio_format = data.get("audio_format", "mp3")
    include_subs = data.get("include_subs", "false").lower() == "true"
    sub_langs = data.get("sub_langs", "all")
    outtmpl = f"{DOWNLOAD_DIR}/{filename}.%(ext)s"

    if media == "audio":
        local_file = f"{DOWNLOAD_DIR}/{filename}.{audio_format}"
        cmd = [
            "yt-dlp",
            "--js-runtimes", "node",
            "--remote-components", "ejs:github",
            "--force-ipv4",
            "--geo-bypass",
            "--no-progress",
            "-f", "bestaudio/best",
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
            "--remote-components", "ejs:github",
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
            "--remote-components", "ejs:github",
            "--force-ipv4",
            "--geo-bypass",
            "--no-progress",
            "-f", data.get("format") or "bv*+ba/b",
            "--merge-output-format", "mp4",
            "-o", outtmpl,
            data["url"]
        ]

    if include_subs:
        print(f"[DEBUG] Subtitles requested. Languages: {sub_langs}")
        subs_flags = [
            "--write-subs",
            "--write-auto-subs",
            "--sub-format", "srt",
            "--sub-langs", sub_langs,
            "--embed-subs",
            "--compat-options", "no-keep-subs-on-embed" 
        ]
        # Remove --embed-subs if prefer separate files. 
        # Requirement: "hasilnya bisa ada srt". Separate files are safer for manipulation.
        # Let's use separate files, so NO --embed-subs.
        subs_flags = [
             "--write-subs",
             "--write-auto-subs",
             "--sub-format", "srt",
             "--sub-langs", sub_langs,
        ]

        if media == "both":
            if 'video_cmd' in locals() and video_cmd is not None:
                video_cmd.extend(subs_flags)
        else:
            if cmd is not None:
                cmd.extend(subs_flags)

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

    # run commands depending on requested media
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

        public_video = ""
        public_audio = ""
        if minio_client:
            try:
                obj_name_v = os.path.basename(video_file)
                minio_client.fput_object(MINIO_BUCKET, obj_name_v, video_file)
                public_video = f"{MINIO_PUBLIC_BASE_URL}/{obj_name_v}" if MINIO_PUBLIC_BASE_URL else ""
            except Exception as e:
                print(f"[WARN] upload video to minio failed: {e}")

            try:
                obj_name_a = os.path.basename(audio_file)
                minio_client.fput_object(MINIO_BUCKET, obj_name_a, audio_file)
                public_audio = f"{MINIO_PUBLIC_BASE_URL}/{obj_name_a}" if MINIO_PUBLIC_BASE_URL else ""
            except Exception as e:
                print(f"[WARN] upload audio to minio failed: {e}")



        # Subtitle handling for 'both' case
        subtitles_map = {}
        local_subtitles_map = {}
        if include_subs:
            try:
                print(f"[DEBUG] Scanning subs in {DOWNLOAD_DIR} for {filename}")
                all_files = os.listdir(DOWNLOAD_DIR)
                print(f"[DEBUG] Found files: {all_files}")
                for f in all_files:
                    if f.startswith(filename) and f.endswith(".srt"):
                        local_sub_path = f"{DOWNLOAD_DIR}/{f}"
                        public_sub_url = ""
                        
                        # Store local path before potential deletion
                        # (Key can be filename or lang code if parsed, but filename is unique)
                        local_subtitles_map[f] = local_sub_path

                        if minio_client:
                            try:
                                minio_client.fput_object(MINIO_BUCKET, f, local_sub_path)
                                public_sub_url = f"{MINIO_PUBLIC_BASE_URL}/{f}" if MINIO_PUBLIC_BASE_URL else ""
                            except Exception as e:
                                print(f"[WARN] upload sub {f} failed: {e}")
                        
                        subtitles_map[f] = public_sub_url
                        if AUTO_DELETE_LOCAL:
                            os.remove(local_sub_path)
            except Exception as e:
                print(f"[WARN] Error handling subtitles: {e}")

        r_local.hset(f"job:{job_id}", mapping={
            "status": "done",
            "storage": "minio" if minio_client else "local",
            "video_file": video_file,
            "audio_file": audio_file,
            "public_video": public_video,
            "public_audio": public_audio,
            "public_subtitles": json.dumps(subtitles_map),
            "subtitles_file": json.dumps(local_subtitles_map)
        })

        if AUTO_DELETE_LOCAL:
            if os.path.exists(video_file):
                os.remove(video_file)
            if os.path.exists(audio_file):
                os.remove(audio_file)
    else:
        subprocess.check_call(cmd)
        public_url = ""
        if minio_client:
            try:
                obj_name = os.path.basename(local_file)
                minio_client.fput_object(MINIO_BUCKET, obj_name, local_file)
                public_url = f"{MINIO_PUBLIC_BASE_URL}/{obj_name}" if MINIO_PUBLIC_BASE_URL else ""
            except Exception as e:
                print(f"[WARN] upload to minio failed: {e}")



        subtitles_map = {}
        local_subtitles_map = {}
        if include_subs:
            try:
                print(f"[DEBUG] Scanning subs in {DOWNLOAD_DIR} for {filename}")
                all_files = os.listdir(DOWNLOAD_DIR)
                print(f"[DEBUG] Found files: {all_files}")
                for f in all_files:
                    if f.startswith(filename) and f.endswith(".srt"):
                        local_sub_path = f"{DOWNLOAD_DIR}/{f}"
                        public_sub_url = ""
                        local_subtitles_map[f] = local_sub_path

                        if minio_client:
                            try:
                                minio_client.fput_object(MINIO_BUCKET, f, local_sub_path)
                                public_sub_url = f"{MINIO_PUBLIC_BASE_URL}/{f}" if MINIO_PUBLIC_BASE_URL else ""
                            except Exception as e:
                                print(f"[WARN] upload sub {f} failed: {e}")
                        
                        subtitles_map[f] = public_sub_url
                        if AUTO_DELETE_LOCAL:
                            os.remove(local_sub_path)
            except Exception as e:
                print(f"[WARN] Error handling subtitles: {e}")

        r_local.hset(f"job:{job_id}", mapping={
            "status": "done",
            "storage": "minio" if minio_client else "local",
            "filename": filename,
            "ext": os.path.splitext(local_file)[1].lstrip('.'),
            "public_url": public_url,
            "public_subtitles": json.dumps(subtitles_map),
            "subtitles_file": json.dumps(local_subtitles_map)
        })

        if AUTO_DELETE_LOCAL and os.path.exists(local_file):
            os.remove(local_file)
    
    return True


def worker_process():
    """
    Worker process that continuously polls Redis queue for jobs.
    This runs in a separate process when using multiprocessing.
    """
    r_local = get_redis_connection()
    print(f"[INFO] Worker process {os.getpid()} started")
    
    while True:
        try:
            job = r_local.brpop("yt_queue", timeout=5)
            if not job:
                continue

            job_id = job[1]
            print(f"[INFO] Worker {os.getpid()} picked up job {job_id}")
            
            process_single_job(job_id)
            
        except KeyboardInterrupt:
            print(f"[INFO] Worker {os.getpid()} shutting down...")
            break
        except Exception as e:
            print(f"[ERROR] Worker {os.getpid()} encountered error: {e}")
            time.sleep(1)


def main():
    """Main entry point for the worker"""
    print(f"▶ YT-DLP WORKER READY")
    print(f"[CONFIG] Concurrency: {WORKER_CONCURRENCY}")
    print(f"[CONFIG] Max Retries: {MAX_RETRIES}")
    print(f"[CONFIG] Job Timeout: {JOB_TIMEOUT}s")
    print(f"[CONFIG] Retry Backoff Base: {RETRY_BACKOFF_BASE}s")
    
    if WORKER_CONCURRENCY == 1:
        # Single worker mode (backward compatible)
        print("[INFO] Running in single-worker mode")
        worker_process()
    else:
        # Multi-worker mode with multiprocessing
        print(f"[INFO] Starting {WORKER_CONCURRENCY} worker processes")
        processes = []
        
        try:
            for i in range(WORKER_CONCURRENCY):
                p = multiprocessing.Process(target=worker_process, name=f"Worker-{i+1}")
                p.start()
                processes.append(p)
                print(f"[INFO] Started worker process {p.pid}")
            
            # Wait for all processes
            for p in processes:
                p.join()
                
        except KeyboardInterrupt:
            print("\n[INFO] Shutting down workers...")
            for p in processes:
                p.terminate()
            for p in processes:
                p.join()
            print("[INFO] All workers stopped")


if __name__ == "__main__":
    main()
