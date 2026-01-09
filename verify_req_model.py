from pydantic import BaseModel
import json

class DownloadReq(BaseModel):
    url: str
    video: bool = True
    audio: bool = False
    transcribe: bool = False
    callback_url: str | None = None
    db_id: str | None = None

# User payload
payload_json = """
{
  "url": "https://youtu.be/example",
  "video": "true",
  "audio": "true",
  "transcribe" :"true", 
  "db_id":"1",
  "callback_url" : "http://n8n:5678/webhook/61b2d375-c9b9-4e3f-baae"
}
"""

print("--- Parsing Payload ---")
try:
    data = json.loads(payload_json)
    req = DownloadReq(**data)
    print(f"Success! Model parsed:")
    print(f"  video (bool): {req.video} (Type: {type(req.video)})")
    print(f"  audio (bool): {req.audio} (Type: {type(req.audio)})")
    print(f"  transcribe (bool): {req.transcribe} (Type: {type(req.transcribe)})")
    print(f"  db_id (str): {req.db_id}")
    print(f"  callback_url (str): {req.callback_url}")

    print("\n--- Logic Verification ---")
    media = "video"
    if req.video and req.audio:
        media = "both"
    elif req.audio:
        media = "audio"
    elif req.video:
        media = "video"
    print(f"Derived 'media': {media}")
    
    redis_mapping = {
        "transcribe": "true" if req.transcribe else "false",
        "transcribe_lang": "id" if req.transcribe else "",
        "audio_format": "mp3" if req.audio else "wav"
    }
    print(f"Redis Mapping: {json.dumps(redis_mapping, indent=2)}")

except Exception as e:
    print(f"Validation Error: {e}")
