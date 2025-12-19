import os, json, uuid, subprocess
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="yt-dlp API")

DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/downloads")
COOKIES_FILE = os.getenv("COOKIES_FILE", "")  # optional: /cookies/cookies.txt
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

class DownloadReq(BaseModel):
    url: str = Field(..., description="YouTube/video URL")
    format: str = Field("bestvideo+bestaudio/best", description="yt-dlp format selector")
    audio_only: bool = False
    filename: str | None = None  # optional fixed name without extension
    extra_args: list[str] = []   # optional extra yt-dlp args

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/download")
def download(req: DownloadReq):
    job_id = str(uuid.uuid4())
    base = req.filename or job_id

    outtmpl = os.path.join(DOWNLOAD_DIR, f"{base}.%(ext)s")

    cmd = [
        "yt-dlp",
        "--no-progress",
        "--print-json",
        "-f", req.format,
        "-o", outtmpl,
        "--merge-output-format", "mp4",
    ]

    # cookies optional
    if COOKIES_FILE:
        cmd += ["--cookies", COOKIES_FILE]

    # audio only
    if req.audio_only:
        cmd = [
            "yt-dlp",
            "--no-progress",
            "--print-json",
            "-x",
            "--audio-format", "mp3",
            "-o", outtmpl,
        ]
        if COOKIES_FILE:
            cmd += ["--cookies", COOKIES_FILE]

    # extra args (misal: --proxy, --geo-bypass, --cookies-from-browser TIDAK disarankan di container)
    if req.extra_args:
        cmd += req.extra_args

    cmd.append(req.url)

    try:
        p = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if p.returncode != 0:
        raise HTTPException(
            status_code=400,
            detail={"error": "yt-dlp failed", "stderr": p.stderr[-4000:], "cmd": cmd},
        )

    # yt-dlp --print-json bisa output beberapa line json (playlist), ambil line terakhir json valid
    info = None
    for line in reversed(p.stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            info = json.loads(line)
            break
        except Exception:
            continue

    if not info:
        info = {"raw": p.stdout[-4000:]}

    # Best guess file path: yt-dlp tidak selalu kasih final path, tapi ada _filename kadang
    file_path = info.get("_filename", "")
    return {
        "job_id": job_id,
        "title": info.get("title"),
        "webpage_url": info.get("webpage_url"),
        "id": info.get("id"),
        "extractor": info.get("extractor"),
        "duration": info.get("duration"),
        "filename": file_path,
        "download_dir": DOWNLOAD_DIR,
        "info": info,
    }
