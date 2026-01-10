import urllib.request
import json
import sys

API_URL = "http://localhost:8000"

def reproduce_empty_url():
    print("Attempting to enqueue job with empty URL...")
    payload = {
        "url": "",
        "video": True,
        "transcribe": False
    }
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(f"{API_URL}/enqueue", data=data, method="POST")
    req.add_header('Content-Type', 'application/json')
    
    try:
        with urllib.request.urlopen(req) as response:
            status_code = response.getcode()
            body = response.read().decode('utf-8')
            if status_code == 200:
                 print(f"FAILED: Server accepted empty URL. Status: {status_code}, Body: {body}")
            else:
                 print(f"Unexpected status: {status_code}, Body: {body}")

    except urllib.request.HTTPError as e:
        status_code = e.code
        body = e.read().decode('utf-8')
        if status_code == 400:
             print(f"SUCCESS: Server correctly rejected empty URL. Code: 400, Body: {body}")
        else:
             print(f"FAILED: Server rejected but with unexpected code. Status: {status_code}, Body: {body}")
    except Exception as e:
        print(f"ERROR: Could not connect to API or other error: {e}")

if __name__ == "__main__":
    reproduce_empty_url()
