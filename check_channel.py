#!/usr/bin/env python3
"""check_channel.py

Usage: set env REDIS_URL if needed, then run:
python check_channel.py "https://www.youtube.com/channel/.../videos"

This script uses `yt-dlp --flat-playlist --dump-json` to list items in a
channel's uploads, stores seen video ids in Redis (per-channel set), and
reports new videos.
"""
import sys
import os
import uuid
import json
import hashlib
import subprocess
import redis
import argparse

REDIS_URL = os.getenv("REDIS_URL", "redis://yt-redis:6379/0")
try:
    r = redis.from_url(REDIS_URL, decode_responses=True)
    r.ping()
except Exception as e:
    print(f"[WARN] Redis connection test failed: {e}", file=sys.stderr)
COOKIES_PATH = os.getenv("COOKIES_PATH", "/data/cookies/cookies.txt")

def channel_key(url: str) -> str:
    # stable key per channel url
    h = hashlib.sha1(url.encode('utf-8')).hexdigest()
    return f"seen:channel:{h}"

def run_yt_dl_flat(channel_url: str):
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-json",
        "--socket-timeout", "15",
        "--",
        channel_url
    ]
    if COOKIES_PATH and os.path.exists(COOKIES_PATH):
        cmd.insert(1, "--cookies")
        cmd.insert(2, COOKIES_PATH)

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

def check_video_has_subtitles(url: str) -> bool:
    cmd = [
        "yt-dlp",
        "--dump-json",
        "--no-playlist",
        "--socket-timeout", "15",
        "--",
        url,
    ]
    if COOKIES_PATH and os.path.exists(COOKIES_PATH):
        cmd.insert(1, "--cookies")
        cmd.insert(2, COOKIES_PATH)
        
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, _ = proc.communicate()
        if proc.returncode == 0 and stdout:
            info = json.loads(stdout)
            return bool(info.get("subtitles") or info.get("automatic_captions"))
    except Exception as e:
        print(f"[WARN] Error checking subtitles for {url}: {e}", file=sys.stderr)
    return False

def process_video(video_obj, seen_set, do_track=True):
    # yt-dlp flat entries usually contain 'id' and 'title'
    vid = video_obj.get('id') or video_obj.get('url')
    if not vid:
        return False

    # Filter: Skip Live, Upcoming, Shorts, and Short Videos (< 15 mins)
    if video_obj.get("live_status") in ("is_live", "is_upcoming"):
        return False
        
    if "/shorts/" in (video_obj.get("url") or ""):
        return False
        
    if (video_obj.get("duration") or 0) < 900:
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
    duration = video_obj.get('duration')

    if do_track:
        # mark seen
        r.sadd(seen_set, vid)

    # Check subtitles
    has_subtitles = check_video_has_subtitles(video_url)

    # just report what was found
    return {"url": video_url, "upload_date": upload_date, "title": title, "has_subtitles": has_subtitles, "duration": duration}

def main():
    p = argparse.ArgumentParser(description="Check a YouTube channel for new videos (flat-playlist).")
    p.add_argument("channel_url")
    p.add_argument("--limit", "-n", type=int, default=1, help="maximum new videos to report/enqueue (default: 1, newest only)")
    p.add_argument("--track", action="store_true", help="mark as seen and enqueue into queue (requires this flag)")
    p.add_argument("--output", "-o", type=str, help="write results to this file (newline-separated URLs or JSON)")
    p.add_argument("--json", action="store_true", help="output JSON format instead of plain URLs")
    args = p.parse_args()

    channel_url = args.channel_url
    limit = args.limit
    do_track = args.track

    seen = channel_key(channel_url)

    results = []
    new_count = 0
    for item in run_yt_dl_flat(channel_url):
        try:            
            info = process_video(item, seen, do_track=do_track)
            if info:
                # info is dict when returned
                results.append(info)
                new_count += 1
                if new_count >= limit:
                    break
        except Exception as e:
            print(f"[WARN] enqueue failed: {e}")

    # output results
    out = {"new_count": new_count, "video_urls": results}

    if args.json:
        # JSON output
        text = json.dumps(out, ensure_ascii=False)
        print(text)
        if args.output:
            try:
                with open(args.output, 'w', encoding='utf-8') as f:
                    f.write(text)
                print(f"Wrote JSON to {args.output}", file=sys.stderr)
            except Exception as e:
                print(f"[WARN] failed to write output file: {e}", file=sys.stderr)
    else:
        # Default: plain URLs (one per line)
        urls = [it.get('url') for it in results]
        for u in urls:
            print(u)
        if args.output:
            try:
                with open(args.output, 'w', encoding='utf-8') as f:
                    f.write("\n".join(urls))
                print(f"Wrote URLs to {args.output}", file=sys.stderr)
            except Exception as e:
                print(f"[WARN] failed to write output file: {e}", file=sys.stderr)

if __name__ == '__main__':
    main()
