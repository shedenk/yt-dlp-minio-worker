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
import argparse

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

def enqueue_video(video_obj, seen_set, do_enqueue=True):
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

    upload_date = video_obj.get('upload_date') or video_obj.get('timestamp')
    title = video_obj.get('title') or ""

    if do_enqueue:
        # mark seen
        r.sadd(seen_set, vid)

        # create a job (include upload_date if available)
        job_id = str(uuid.uuid4())
        mapping = {
            "status": "queued",
            "url": video_url,
            "filename": vid,
            "format": "",
            "media": "video",
            "upload_date": upload_date,
            "title": title,
        }
        clean_mapping = {k: str(v) for k, v in mapping.items() if v is not None}
        r.hset(f"job:{job_id}", mapping=clean_mapping)
        r.lpush("yt_queue", job_id)
        return {"job_id": job_id, "url": video_url, "upload_date": upload_date, "title": title}
    else:
        # dry-run: just report what would be enqueued
        return {"job_id": None, "url": video_url, "upload_date": upload_date, "title": title}

def main():
    p = argparse.ArgumentParser(description="Check a YouTube channel for new videos (flat-playlist).")
    p.add_argument("channel_url")
    p.add_argument("--limit", "-n", type=int, default=1, help="maximum new videos to report/enqueue")
    p.add_argument("--dry-run", action="store_true", help="do not mark seen or enqueue; only list new videos")
    p.add_argument("--output", "-o", type=str, help="write JSON results to this file")
    p.add_argument("--urls-only", action="store_true", help="print only the new video URLs (one per line)")
    args = p.parse_args()

    channel_url = args.channel_url
    limit = args.limit
    dry_run = args.dry_run

    seen = channel_key(channel_url)

    results = []
    new_count = 0
    for item in run_yt_dl_flat(channel_url):
        try:
            info = enqueue_video(item, seen, do_enqueue=not dry_run)
            if info:
                # info is dict when returned
                results.append(info)
                new_count += 1
                if new_count >= limit:
                    break
        except Exception as e:
            print(f"[WARN] enqueue failed: {e}")

    # output results
    out = {"new_count": new_count, "items": results}

    if args.urls_only:
        urls = [it.get('url') for it in results]
        # print one URL per line
        for u in urls:
            print(u)
        if args.output:
            try:
                with open(args.output, 'w', encoding='utf-8') as f:
                    f.write("\n".join(urls))
                print(f"Wrote URLs to {args.output}")
            except Exception as e:
                print(f"[WARN] failed to write output file: {e}")
    else:
        text = json.dumps(out, ensure_ascii=False)
        print(text)
        if args.output:
            try:
                with open(args.output, 'w', encoding='utf-8') as f:
                    f.write(text)
                print(f"Wrote results to {args.output}")
            except Exception as e:
                print(f"[WARN] failed to write output file: {e}")

if __name__ == '__main__':
    main()
