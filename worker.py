import os, time, subprocess, redis, signal, multiprocessing, json, sys
print("[DEBUG] worker.py: imports done")
sys.stdout.flush()

try:
    from faster_whisper import WhisperModel
    WHISPER_MODEL_NAME = os.getenv("WHISPER_MODEL", "base")
    print(f"[INFO] Found faster-whisper package. Model: {WHISPER_MODEL_NAME}")
    sys.stdout.flush()
    
    class WhisperingModel:
        _instance = None
        def __new__(cls):
            if cls._instance is None:
                # Run on CPU by default for stability in containers, or 'cuda' if available
                device = "cuda" if os.getenv("USE_GPU", "false").lower() == "true" else "cpu"
                print(f"[INFO] Loading Faster-Whisper model '{WHISPER_MODEL_NAME}' on {device}...")
                cls._instance = WhisperModel(WHISPER_MODEL_NAME, device=device, compute_type="int8")
            return cls._instance

    # We'll instantiate it on demand to save memory if transcription is never used
    get_whisper_model = WhisperingModel
except ImportError:
    get_whisper_model = lambda: None
    print("[WARN] faster-whisper package not found; transcription disabled")
except Exception as e:
    get_whisper_model = lambda: None
    print(f"[ERROR] Failed to load Whisper model: {e}")
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

REDIS_URL = os.getenv("REDIS_URL", "redis://yt-redis:6379/0")
print(f"[INFO] Initializing worker with REDIS_URL: {REDIS_URL.split('@')[1] if '@' in REDIS_URL else REDIS_URL}")

def get_redis_client(url: str):
    # Mask password for logging
    masked_url = url
    pwd_len = 0
    if "://" in url and "@" in url:
        try:
            auth_part = url.split("://")[1].rsplit("@", 1)[0]
            if ":" in auth_part:
                pwd = auth_part.split(":", 1)[1]
                pwd_len = len(pwd)
                masked_url = url.replace(pwd, "****")
            else:
                pwd_len = len(auth_part)
                masked_url = url.replace(auth_part, "****")
        except Exception:
            masked_url = "redis://****@..."
    
    try:
        client = redis.from_url(url, decode_responses=True)
        print(f"[INFO] Connecting to Redis at {masked_url} (host: {client.connection_pool.connection_kwargs.get('host')}, port: {client.connection_pool.connection_kwargs.get('port')}, password length: {pwd_len})...")
        client.ping()
        print("[INFO] Redis connection successful")
        return client
    except redis.exceptions.AuthenticationError:
        print("[CRITICAL] Redis Authentication failed! The provided password is incorrect.")
        import sys
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Redis connection failed: {e}")
        return client

r = get_redis_client(REDIS_URL)
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/data/downloads")
COOKIES_PATH = os.getenv("COOKIES_PATH", "/data/cookies/cookies.txt")
AUTO_DELETE_LOCAL = os.getenv("AUTO_DELETE_LOCAL", "true").lower() == "true"

# New configuration for retry and concurrency
WORKER_CONCURRENCY = int(os.getenv("WORKER_CONCURRENCY", "3"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
JOB_TIMEOUT = int(os.getenv("JOB_TIMEOUT", "7200"))  # 2 hours default
RETRY_BACKOFF_BASE = int(os.getenv("RETRY_BACKOFF_BASE", "60"))  # 60 seconds

# Redundant init removed
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


def run_subprocess_safe(cmd):
    """Run a subprocess and ensure it is killed if an exception (like Timeout) occurs."""
    print(f"[INFO] Running command: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd)
    try:
        proc.wait()
    except Exception:
        if proc.poll() is None:
            print(f"[WARN] Killing stuck subprocess {proc.pid}")
            proc.kill()
            proc.wait()
        raise
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd)

def run_command_with_progress(cmd, job_id, r_local, stage="downloading"):
    """Run a command and parse yt-dlp progress output."""
    import re
    print(f"[INFO] Running command: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    
    percent_re = re.compile(r"(\d+(?:\.\d+)?)%")
    error_lines = []

    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            
            # Look for percentage in yt-dlp output
            match = percent_re.search(line)
            if match:
                percent_str = match.group(1)
                try:
                    # Ensure it's a valid number between 0 and 100
                    percent = float(percent_str)
                    if 0 <= percent <= 100:
                        print(f"[{stage.upper()} PROGRESS] {percent_str}%")
                        r_local.hset(f"job:{job_id}", mapping={
                            "status": f"{stage} ({percent_str}%)",
                            "progress": percent_str,
                            "heartbeat": int(time.time())
                        })
                except ValueError:
                    pass
            else:
                # Log non-progress lines for debugging (errors, info, etc)
                print(f"[YTDLP] {line}")
                if "ERROR:" in line:
                    error_lines.append(line)
                sys.stdout.flush()

        proc.wait()
    except Exception:
        if proc.poll() is None:
            print(f"[WARN] Killing stuck subprocess {proc.pid}")
            proc.kill()
            proc.wait()
        raise

    if proc.returncode != 0:
        print(f"[ERROR] Command failed with return code {proc.returncode}")
        if error_lines:
            raise Exception(f"Download failed: {'; '.join(error_lines)}")
        raise subprocess.CalledProcessError(proc.returncode, cmd)
    return True


def _format_timestamp(seconds: float) -> str:
    """Format seconds into SRT timestamp format: HH:MM:SS,mmm"""
    td = time.gmtime(seconds)
    ms = int((seconds % 1) * 1000)
    return f"{time.strftime('%H:%M:%S', td)},{ms:03d}"


def _to_srt(segments) -> str:
    """Convert Faster-Whisper segments generator/list to SRT string."""
    srt = []
    for i, seg in enumerate(segments, 1):
        # Faster-Whisper segments have start, end, text attributes
        start = _format_timestamp(seg.start)
        end = _format_timestamp(seg.end)
        text = seg.text.strip()
        if text:
            srt.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(srt)


def _transcribe_audio(audio_path: str, job_id: str, r_local: redis.Redis, lang: str = None, prompt: str = None) -> Optional[str]:
    """Transcribe audio file using Faster-Whisper and return content in SRT format."""
    model = get_whisper_model()
    if not model or not os.path.exists(audio_path):
        return None
    try:
        print(f"[INFO] Transcribing {audio_path} (lang={lang})...")
        segments, info = model.transcribe(
            audio_path, 
            language=lang, 
            initial_prompt=prompt, 
            beam_size=1,            # Faster on CPU
            best_of=1,              # Match beam_size
            vad_filter=True,        # Skip silences to avoid stalls
            vad_parameters=dict(min_silence_duration_ms=500),
            temperature=0           # More stable decoding
        )
        
        duration = getattr(info, 'duration', 0)
        duration_after_vad = getattr(info, 'duration_after_vad', duration)
        print(f"[INFO] Audio duration: {duration:.2f}s (after VAD: {duration_after_vad:.2f}s)")
        print(f"[INFO] Detected language: {info.language} ({info.language_probability:.2f})")
        
        srt_segments = []
        last_update = time.time()
        
        # Iterate through segments to provide progress updates
        for i, seg in enumerate(segments, 1):
            srt_segments.append(seg)
            
            # Detailed per-segment logging for debugging stalls
            print(f"[TRANSCRIPTION-SEGMENT] {seg.start:.1f}s - {seg.end:.1f}s: {seg.text.strip()}")
            
            # Update Redis status every 10 seconds with progress and heartbeat
            now = time.time()
            if now - last_update > 10:
                progress = (seg.end / duration * 100) if duration > 0 else 0
                progress_str = f"{progress:.1f}"
                print(f"[TRANSCRIBING PROGRESS] {progress_str}%")
                r_local.hset(f"job:{job_id}", mapping={
                    "status": f"transcribing ({progress_str}%)",
                    "progress": progress_str,
                    "heartbeat": int(now)
                })
                last_update = now
                
        if not srt_segments:
            print("[WARN] No segments found during transcription")
            return None
            
        return _to_srt(srt_segments)
    except Exception as e:
        print(f"[ERROR] Transcription failed: {e}")
        return None


def _trigger_callback(job_id: str, r_local: redis.Redis):
    """Fetch job data and POST it to callback_url if present."""
    import httpx
    try:
        data = r_local.hgetall(f"job:{job_id}")
        callback_url = data.get("callback_url")
        if not callback_url:
            return

        print(f"[CALLBACK] Triggering for job {job_id} to {callback_url}")
        
        # Prepare payload: include job_id, exclude heartbeat
        payload = data.copy()
        payload["job_id"] = job_id
        if "heartbeat" in payload:
            del payload["heartbeat"]

        print(f"[CALLBACK] Body: {json.dumps(payload)}")
        sys.stdout.flush()

        with httpx.Client(timeout=30.0) as client:
            resp = client.post(callback_url, json=payload)
            print(f"[CALLBACK] Status: {resp.status_code}")
            if resp.status_code >= 400:
                print(f"[CALLBACK] Response Body: {resp.text}")
    except Exception as e:
        print(f"[CALLBACK] Error: {e}")


def _upload_file_to_minio(file_path: str, bucket_name: str) -> str:
    """Upload a file to MinIO and return its public URL."""
    if not minio_client or not os.path.exists(file_path):
        return ""
    try:
        obj_name = os.path.basename(file_path)
        minio_client.fput_object(bucket_name, obj_name, file_path)
        return f"{MINIO_PUBLIC_BASE_URL}/{obj_name}" if MINIO_PUBLIC_BASE_URL else ""
    except Exception as e:
        print(f"[WARN] Upload to MinIO failed for {file_path}: {e}")
        return ""


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
                
            # Trigger callback regardless of success/fail (terminal state reached)
            _trigger_callback(job_id, r_local)

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
                
                # Final failure callback
                _trigger_callback(job_id, r_local)
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
    
    # Initialize progress
    r_local.hset(f"job:{job_id}", "progress", "0")
    filename = data.get("filename", job_id)
    media = data.get("media", "video")
    audio_format = data.get("audio_format", "mp3")
    include_subs = data.get("include_subs", "false").lower() == "true"
    sub_langs = data.get("sub_langs", "all")
    should_transcribe = data.get("transcribe", "false").lower() == "true"
    transcribe_lang = data.get("transcribe_lang") or None
    transcribe_prompt = data.get("transcribe_prompt") or None
    outtmpl = f"{DOWNLOAD_DIR}/{filename}.%(ext)s"
    
    # Get metadata including duration and quality
    duration = 0
    video_quality = ""
    video_fps = ""
    audio_quality = ""
    try:
        if COOKIES_PATH:
            if os.path.exists(COOKIES_PATH):
                print(f"[INFO] Using cookies from {COOKIES_PATH} (size: {os.path.getsize(COOKIES_PATH)} bytes)")
            else:
                print(f"[WARN] Cookies file NOT FOUND at {COOKIES_PATH}")
        
        meta_cmd = ["yt-dlp", "--dump-json", "--flat-playlist", "--", data["url"]]
        if COOKIES_PATH and os.path.exists(COOKIES_PATH):
            meta_cmd.insert(1, "--cookies")
            meta_cmd.insert(2, COOKIES_PATH)
        meta_proc = subprocess.Popen(meta_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        meta_out, _ = meta_proc.communicate()
        if meta_out:
            meta = json.loads(meta_out)
            
            # Check for upcoming live streams (waiting for live)
            if meta.get("live_status") == "is_upcoming":
                raise Exception("Video is an upcoming live stream (waiting for live).")
            
            # Check for Shorts (if not caught by API URL check)
            if "/shorts/" in meta.get("webpage_url", ""):
                raise Exception("Video is a YouTube Short.")

            duration = meta.get("duration") or 0
            v_height = meta.get("height") or 0
            v_fps = meta.get("fps") or 0
            a_abr = meta.get("abr") or 0
            
            # Simple quality mapping
            if v_height >= 2160: video_quality = "4k"
            elif v_height >= 1440: video_quality = "2k"
            elif v_height >= 1080: video_quality = "1080p"
            elif v_height >= 720: video_quality = "720p"
            else: video_quality = f"{v_height}p" if v_height else ""
            
            if v_fps: video_fps = str(int(v_fps))
            if a_abr: audio_quality = f"{int(a_abr)}kbps"
    except Exception as e:
        print(f"[WARN] Failed to get video metadata: {e}")

    if media == "audio":
        local_file = f"{DOWNLOAD_DIR}/{filename}.{audio_format}"
        cmd = [
            "yt-dlp",
            "--js-runtimes", "node",
            "--remote-components", "ejs:github",
            "--force-ipv4",
            "--geo-bypass",
            # "--no-progress", # removed to allow parsing
            "-f", "bestaudio/best",
            "-x",
            "--audio-format", audio_format,
            "-o", outtmpl,
            "--",
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
            # "--no-progress", # removed to allow parsing
            "-f", data.get("format") or "bv*+ba/b",
            "--merge-output-format", "mp4",
            "-o", outtmpl,
            "--",
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
            # "--no-progress", # removed to allow parsing
            "-f", data.get("format") or "bv*+ba/b",
            "--merge-output-format", "mp4",
            "-o", outtmpl,
            "--",
            data["url"]
        ]

    if include_subs:
        print(f"[DEBUG] Subtitles requested. Languages: {sub_langs}")
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
        run_command_with_progress(video_cmd, job_id, r_local, stage="downloading")
        # extract audio using ffmpeg
        try:
            run_subprocess_safe(["ffmpeg", "-y", "-i", video_file, audio_file])
        except Exception:
            # fallback: try yt-dlp audio extraction if ffmpeg fails
            fallback_cmd = [
                "yt-dlp", "-x", "--audio-format", audio_format, "-o", outtmpl, "--", data["url"]
            ]
            if COOKIES_PATH and os.path.exists(COOKIES_PATH):
                fallback_cmd.insert(1, "--cookies")
                fallback_cmd.insert(2, COOKIES_PATH)
            run_subprocess_safe(fallback_cmd)

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

        # Transcription handling for 'both'
        public_transcript = ""
        local_transcript_path = ""
        if should_transcribe and os.path.exists(audio_file):
            r_local.hset(f"job:{job_id}", "status", "transcribing (0%)")
            text = _transcribe_audio(audio_file, job_id, r_local, lang=transcribe_lang, prompt=transcribe_prompt)
            if text:
                local_transcript_path = f"{DOWNLOAD_DIR}/{filename}.srt"
                with open(local_transcript_path, "w", encoding="utf-8") as f:
                    f.write(text)
                public_transcript = _upload_file_to_minio(local_transcript_path, MINIO_BUCKET)
            else:
                r_local.hset(f"job:{job_id}", "whisper_error", "Transcription returned empty result or failed")

        r_local.hset(f"job:{job_id}", mapping={
            "status": "done",
            "progress": "100",
            "video_file": public_video,
            "audio_file": public_audio,
            "transcript_file": public_transcript,
            "duration": duration,
            "video_duration": duration,
            "audio_duration": duration,
            "video_quality": video_quality,
            "video_fps": video_fps,
            "audio_quality": audio_quality
        })

        if AUTO_DELETE_LOCAL:
            print(f"[INFO] Cleaning up local files for {filename}")
            # Delete direct files
            for f in [video_file, audio_file, local_transcript_path]:
                if f and os.path.exists(f):
                    try:
                        os.remove(f)
                    except Exception as e:
                        print(f"[WARN] Failed to delete {f}: {e}")
            
            # Catch any remaining files with this filename prefix (e.g. fragments, extra subs)
            try:
                for f in os.listdir(DOWNLOAD_DIR):
                    if f.startswith(filename):
                        path = os.path.join(DOWNLOAD_DIR, f)
                        if os.path.isfile(path):
                            os.remove(path)
            except Exception as e:
                print(f"[WARN] Batch cleanup failed: {e}")
    else:
        run_command_with_progress(cmd, job_id, r_local, stage="downloading")
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
                # Priority languages if "all" is requested
                priority_langs = ['id', 'en']
                
                print(f"[DEBUG] Scanning subs in {DOWNLOAD_DIR} for {filename}")
                all_files = os.listdir(DOWNLOAD_DIR)
                for f in all_files:
                    # Check if it's a subtitle file for this job
                    # yt-dlp saves as filename.lang.srt
                    if f.startswith(filename) and f.endswith(".srt"):
                        # Skip the main transcription file (it's handled separately)
                        if f == f"{filename}.srt":
                            continue
                            
                        # If "all" was requested, only upload priority languages to save space
                        if sub_langs_requested == "all":
                            lang_part = f.replace(filename + ".", "").replace(".srt", "")
                            if lang_part not in priority_langs:
                                # Still keep locally for the status but don't upload to MinIO
                                # to avoid cluttering as per user request
                                continue

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
                        # Don't delete yet, batch cleanup will handle it
            except Exception as e:
                print(f"[WARN] Error handling subtitles: {e}")

        # Transcription handling for single 'media' (video or audio)
        public_transcript = ""
        local_transcript_path = ""
        if should_transcribe:
            transcript_input = local_file
            temp_audio = ""
            if media == "video":
                # Need to extract audio temporarily for transcription
                temp_audio = f"{DOWNLOAD_DIR}/{filename}_temp.wav"
                try:
                    run_subprocess_safe(["ffmpeg", "-y", "-i", local_file, "-ar", "16000", "-ac", "1", temp_audio])
                    transcript_input = temp_audio
                except Exception as e:
                    print(f"[ERROR] Failed to extract temp audio for transcription: {e}")
                    transcript_input = None

            if transcript_input and os.path.exists(transcript_input):
                r_local.hset(f"job:{job_id}", "status", "transcribing (0%)")
                text = _transcribe_audio(transcript_input, job_id, r_local, lang=transcribe_lang, prompt=transcribe_prompt)
                if text:
                    local_transcript_path = f"{DOWNLOAD_DIR}/{filename}.srt"
                    with open(local_transcript_path, "w", encoding="utf-8") as f:
                        f.write(text)
                    public_transcript = _upload_file_to_minio(local_transcript_path, MINIO_BUCKET)
                else:
                    r_local.hset(f"job:{job_id}", "whisper_error", "Transcription returned empty result or failed")

            if temp_audio and os.path.exists(temp_audio):
                os.remove(temp_audio)

        r_local.hset(f"job:{job_id}", mapping={
            "status": "done",
            "progress": "100",
            "filename": filename,
            "ext": os.path.splitext(local_file)[1].lstrip('.'),
            "public_url": public_url,
            "video_file": public_url if media == "video" else "",
            "audio_file": public_url if media == "audio" else "",
            "transcript_file": public_transcript,
            "duration": duration,
            "video_duration": duration if media == "video" else "0",
            "audio_duration": duration if media == "audio" else "0",
            "video_quality": video_quality if media == "video" else "",
            "video_fps": video_fps if media == "video" else "",
            "audio_quality": audio_quality if media == "audio" else ""
        })

        if AUTO_DELETE_LOCAL:
            print(f"[INFO] Cleaning up local files for {filename}")
            for f in [local_file, local_transcript_path]:
                if f and os.path.exists(f):
                    try:
                        os.remove(f)
                    except Exception as e:
                        print(f"[WARN] Failed to delete {f}: {e}")
            
            # Catch any remaining files with this filename prefix
            try:
                for f in os.listdir(DOWNLOAD_DIR):
                    if f.startswith(filename):
                        path = os.path.join(DOWNLOAD_DIR, f)
                        if os.path.isfile(path):
                            os.remove(path)
            except Exception as e:
                print(f"[WARN] Batch cleanup failed: {e}")
    
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
            
            # Monitor loop: Restart workers if they die
            while True:
                time.sleep(5)
                for i, p in enumerate(processes):
                    if not p.is_alive():
                        print(f"[WARN] Worker {p.name} (pid {p.pid}) died. Restarting...")
                        new_p = multiprocessing.Process(target=worker_process, name=p.name)
                        new_p.start()
                        processes[i] = new_p
                        print(f"[INFO] Started new worker process {new_p.pid}")
                
        except KeyboardInterrupt:
            print("\n[INFO] Shutting down workers...")
            for p in processes:
                p.terminate()
            for p in processes:
                p.join()
            print("[INFO] All workers stopped")


if __name__ == "__main__":
    main()
