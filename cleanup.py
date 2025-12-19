import os, time

DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/downloads")
MAX_AGE_SECONDS = int(os.getenv("CLEANUP_MAX_AGE", "7200"))  # default 2 jam
EXTENSIONS = (".mp4", ".mkv", ".webm", ".mp3", ".m4a")

now = time.time()
deleted = []

for root, _, files in os.walk(DOWNLOAD_DIR):
    for f in files:
        if not f.lower().endswith(EXTENSIONS):
            continue

        path = os.path.join(root, f)

        try:
            stat = os.stat(path)
            age = now - stat.st_mtime

            # Skip file baru / aktif
            if age < MAX_AGE_SECONDS:
                continue

            os.remove(path)
            deleted.append(path)

        except Exception as e:
            print(f"[WARN] Skip {path}: {e}")

print(f"[CLEANUP] Deleted {len(deleted)} files")
