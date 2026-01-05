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

def enqueue_video(video_obj, seen_set, do_enqueue=True):
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
            "media": video_obj.get("media", "video"),
            "transcribe": "true" if video_obj.get("transcribe", True) else "false",
            "include_subs": "true" if video_obj.get("include_subs") else "false",
            "sub_langs": video_obj.get("sub_langs", "all"),
            "transcribe_lang": video_obj.get("transcribe_lang", ""),
            "transcribe_prompt": video_obj.get("transcribe_prompt", ""),
            "upload_date": upload_date,
            "title": title,
            "duration": duration,
        }
        clean_mapping = {k: str(v) for k, v in mapping.items() if v is not None}
        r.hset(f"job:{job_id}", mapping=clean_mapping)
        r.lpush("yt_queue", job_id)
        return {"job_id": job_id, "url": video_url, "upload_date": upload_date, "title": title, "duration": duration}
    else:
        # dry-run: just report what would be enqueued
        return {"job_id": None, "url": video_url, "upload_date": upload_date, "title": title, "duration": duration}

def main():
    p = argparse.ArgumentParser(description="Check a YouTube channel for new videos (flat-playlist).")
    p.add_argument("channel_url")
    p.add_argument("--limit", "-n", type=int, default=1, help="maximum new videos to report/enqueue (default: 1, newest only)")
    p.add_argument("--track", action="store_true", help="mark as seen and enqueue into queue (requires this flag)")
    p.add_argument("--output", "-o", type=str, help="write results to this file (newline-separated URLs or JSON)")
    p.add_argument("--json", action="store_true", help="output JSON format instead of plain URLs")
    p.add_argument("--transcribe", action="store_true", default=True, help="enable transcription (default: True)")
    p.add_argument("--no-transcribe", action="store_false", dest="transcribe", help="disable transcription")
    p.add_argument("--media", type=str, default="video", choices=["video", "audio", "both"], help="media type: video, audio, or both (default: video)")
    p.add_argument("--include-subs", action="store_true", help="enable subtitles")
    p.add_argument("--sub-langs", type=str, default="all", help="subtitle languages")
    p.add_argument("--lang", type=str, help="transcription language")
    p.add_argument("--prompt", type=str, help="transcription prompt")
    args = p.parse_args()

    channel_url = args.channel_url
    limit = args.limit
    do_track = args.track

    seen = channel_key(channel_url)

    results = []
    new_count = 0
    for item in run_yt_dl_flat(channel_url):
        try:
            # Add extra params to item for enqueue_video
            item["transcribe"] = args.transcribe
            item["media"] = args.media
            item["include_subs"] = args.include_subs
            item["sub_langs"] = args.sub_langs
            item["transcribe_lang"] = args.lang
            item["transcribe_prompt"] = args.prompt
            
            info = enqueue_video(item, seen, do_enqueue=do_track)
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
