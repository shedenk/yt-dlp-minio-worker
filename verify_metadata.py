
import unittest
from unittest.mock import MagicMock, patch
import json
import subprocess
import sys

# Mock modules
sys.modules["redis"] = MagicMock()

# Import
with patch.dict("os.environ", {"REDIS_URL": "redis://mock:6379/0"}):
    from check_channel import get_video_details

class TestVideoDetails(unittest.TestCase):
    @patch("subprocess.Popen")
    def test_upload_date_parsing(self, mock_popen):
        # Setup mock process
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_json = {
            "subtitles": {},
            "upload_date": "20230101",
            "title": "Test Video",
            "duration": 100
        }
        mock_proc.communicate.return_value = (json.dumps(mock_json), "")
        mock_popen.return_value = mock_proc

        # Call function
        details = get_video_details("http://youtube.com/watch?v=123")
        
        # Verify
        self.assertEqual(details["upload_date"], "20230101")
        self.assertEqual(details["title"], "Test Video")
        self.assertEqual(details["duration"], 100)
        print("SUCCESS: Parsed upload_date correctly")

if __name__ == "__main__":
    unittest.main()
