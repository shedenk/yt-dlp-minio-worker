
import sys
import os
from unittest.mock import MagicMock

# Mock redis before importing app
mock_redis_module = MagicMock()
sys.modules["redis"] = mock_redis_module

# Setup mock client
mock_client = MagicMock()
mock_redis_module.from_url.return_value = mock_client
# ping() should succeed
mock_client.ping.return_value = True

# Also need to mock slowapi if it causes issues, but usually it's fine.
# We might need to ensure REDIS_URL is set
os.environ["REDIS_URL"] = "redis://mock:6379/0"

try:
    from app import enqueue, DownloadReq
    from fastapi import HTTPException
except ImportError as e:
    print(f"CRITICAL: Failed to import app: {e}")
    sys.exit(1)
except Exception as e:
    print(f"CRITICAL: Error during app import: {e}")
    sys.exit(1)

def test_empty_url():
    print("Running test_empty_url...")
    # Test case 1: Empty string
    req = DownloadReq(url="", video=True)
    request = MagicMock()
    try:
        enqueue(request, req)
        print("FAILED: Empty string did not raise HTTPException")
    except HTTPException as e:
        if e.status_code == 400:
            print("SUCCESS: Empty string raised HTTPException 400")
        else:
            print(f"FAILED: Empty string raised code {e.status_code}")
    except Exception as e:
        print(f"FAILED: Empty string raised unexpected exception {e}")

    # Test case 2: Whitespace string
    req = DownloadReq(url="   ", video=True)
    try:
        enqueue(request, req)
        print("FAILED: Whitespace string did not raise HTTPException")
    except HTTPException as e:
        if e.status_code == 400:
            print("SUCCESS: Whitespace string raised HTTPException 400")
        else:
            print(f"FAILED: Whitespace string raised code {e.status_code}")
    except Exception as e:
        print(f"FAILED: Whitespace string raised unexpected exception {e}")

if __name__ == "__main__":
    test_empty_url()
