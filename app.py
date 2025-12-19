from flask import Flask, request, jsonify
from minio import Minio
from minio.error import S3Error
import yt_dlp
import os
import tempfile
import logging

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# Konfigurasi dari environment
MINIO_HOST = os.getenv("MINIO_HOST", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "youtube-videos")
USE_COOKIE = os.getenv("USE_COOKIE", "false").lower() == "true"

# Koneksi MinIO
minio_client = Minio(
    MINIO_HOST,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False
)

# Buat bucket jika belum ada
if not minio_client.bucket_exists(MINIO_BUCKET):
    minio_client.make_bucket(MINIO_BUCKET)

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "OK", "bucket": MINIO_BUCKET})

@app.route('/fetch', methods=['POST'])
def fetch_video():
    data = request.get_json()
    url = data.get('url')
    if not url:
        return jsonify({"error": "Parameter 'url' wajib diisi"}), 400

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Opsi yt-dlp
            ydl_opts = {
                'format': 'bv*+ba/b',
                'outtmpl': os.path.join(tmp_dir, '%(id)s.%(ext)s'),
                'quiet': False,
                'noplaylist': True,
            }
            
            # Tambahkan cookie jika diaktifkan
            if USE_COOKIE:
                cookie_path = '/app/cookies/cookies.txt'
                if os.path.exists(cookie_path):
                    ydl_opts['cookiefile'] = cookie_path
                else:
                    logging.warning("Cookie diaktifkan tapi file tidak ditemukan")

            # Download video
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filepath = ydl.prepare_filename(info)
                object_name = f"{info['id']}.{info['ext']}"

            # Upload ke MinIO
            minio_client.fput_object(MINIO_BUCKET, object_name, filepath)
            logging.info(f"Video {object_name} berhasil diupload ke MinIO")

            # Generate pre-signed URL (berlaku 1 jam)
            presigned_url = minio_client.presigned_get_object(
                MINIO_BUCKET, 
                object_name, 
                expires=3600
            )

            return jsonify({
                "success": True,
                "title": info.get('title'),
                "video_id": info.get('id'),
                "channel": info.get('channel'),
                "object_name": object_name,
                "minio_path": f"s3://{MINIO_BUCKET}/{object_name}",
                "download_url": presigned_url
            })

    except Exception as e:
        logging.error(f"Error: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3000, debug=False)