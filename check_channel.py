#!/usr/bin/env python3
"""check_channel.py

Usage: set env REDIS_URL if needed, then run:
python check_channel.py "https://www.youtube.com/channel/.../videos"

This script uses `yt-dlp --flat-playlist --dump-json` to list items in a
channel's uploads, stores seen video ids in Redis (per-channel set), and
enqueues new videos into the `yt_queue` by creating job entries in Redis.
"""
import sys
import os
import uuid
import json
import hashlib
import subprocess
import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
r = redis.from_url(REDIS_URL, decode_responses=True)

def channel_key(url: str) -> str:
    # stable key per channel url
    h = hashlib.sha1(url.encode('utf-8')).hexdigest()
    return f"seen:channel:{h}"

def run_yt_dl_flat(channel_url: str):
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-json",
        channel_url
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            # skip invalid lines
            continue
        yield obj

def enqueue_video(video_obj, seen_set):
    # yt-dlp flat entries usually contain 'id' and 'title'
    vid = video_obj.get('id') or video_obj.get('url')
    if not vid:
        return False

    # use youtube watch URL if id looks like a video id
    if len(vid) <= 32 and not vid.startswith('http'):
        video_url = f"https://www.youtube.com/watch?v={vid}"
    else:
        video_url = video_obj.get('url') or vid

    # check seen
    if r.sismember(seen_set, vid):
        return False

    # mark seen
    r.sadd(seen_set, vid)

    # create a job
    job_id = str(uuid.uuid4())
    r.hset(f"job:{job_id}", mapping={
        "status": "queued",
        "url": video_url,
        "filename": vid,
        "format": "",
        "media": "video"
    })
    r.lpush("yt_queue", job_id)
    return True

def main():
    if len(sys.argv) < 2:
        print("Usage: python check_channel.py <channel_videos_url>")
        sys.exit(2)

    channel_url = sys.argv[1]
    seen = channel_key(channel_url)

    new_count = 0
    for item in run_yt_dl_flat(channel_url):
        try:
            if enqueue_video(item, seen):
                new_count += 1
        except Exception as e:
            print(f"[WARN] enqueue failed: {e}")

    print(f"Done. New videos enqueued: {new_count}")

if __name__ == '__main__':
    main()
