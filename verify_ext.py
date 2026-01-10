
import os
import shutil

DOWNLOAD_DIR = "test_downloads"
filename = "test_job_123"

def setup():
    if os.path.exists(DOWNLOAD_DIR):
        shutil.rmtree(DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR)
    
    # Create a dummy mp3 file
    with open(f"{DOWNLOAD_DIR}/{filename}.mp3", "w") as f:
        f.write("dummy content")

def test_detection():
    print("Testing file detection logic...")
    local_file = None
    try:
        candidates = []
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(filename) and not f.endswith(".part") and not f.endswith(".ytdl") and not f.endswith(".json"):
                if f.startswith(filename + "."):
                    candidates.append(os.path.join(DOWNLOAD_DIR, f))
        
        if candidates:
            local_file = next((f for f in candidates if f.endswith(".mp4")), candidates[0])
            print(f"Detected: {local_file}")
        else:
            print("No file found")
            
    except Exception as e:
        print(f"Error: {e}")
        
    if local_file and local_file.endswith(".mp3"):
        print("SUCCESS: Detected .mp3 file correctly")
    else:
        print(f"FAILED: Expected .mp3, got {local_file}")

def cleanup():
    if os.path.exists(DOWNLOAD_DIR):
        shutil.rmtree(DOWNLOAD_DIR)

if __name__ == "__main__":
    setup()
    test_detection()
    cleanup()
