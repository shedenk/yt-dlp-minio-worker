import os, time

DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/data/downloads")
MAX_AGE = int(os.getenv("CLEANUP_MAX_AGE", "7200"))
EXTS = (".mp4", ".mkv", ".webm", ".mp3", ".m4a", ".wav")

now = time.time()
deleted = 0

for root, _, files in os.walk(DOWNLOAD_DIR):
    for f in files:
        if not f.lower().endswith(EXTS):
            continue

        path = os.path.join(root, f)
        try:
            if now - os.stat(path).st_mtime > MAX_AGE:
                os.remove(path)
                deleted += 1
        except Exception as e:
            print(f"[WARN] {path}: {e}")

print(f"[CLEANUP] deleted={deleted}")
