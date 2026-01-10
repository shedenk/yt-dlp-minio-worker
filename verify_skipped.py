
import sys
import unittest
from unittest.mock import MagicMock, patch

# Mock modules
sys.modules["redis"] = MagicMock()
sys.modules["faster_whisper"] = MagicMock()
sys.modules["minio"] = MagicMock()

# Import the function to test
# We need to mock os.getenv to avoid side effects during import if any
with patch.dict("os.environ", {"REDIS_URL": "redis://mock:6379/0", "DOWNLOAD_DIR": "/tmp"}):
    from worker import process_single_job

class TestSkippedStatus(unittest.TestCase):
    @patch("worker.get_redis_connection")
    @patch("worker._execute_download")
    @patch("worker._trigger_callback")
    def test_members_only_error(self, mock_callback, mock_execute, mock_redis_conn):
        # Setup mocks
        mock_r = MagicMock()
        mock_redis_conn.return_value = mock_r
        
        # Simulate fatal error in _execute_download
        mock_execute.side_effect = Exception("Join this channel to get access to members-only content")
        
        # Run process_single_job
        job_id = "test_job_skipped"
        process_single_job(job_id)
        
        # Verify hset was called with status='skipped'
        # We need to find the call that sets status to skipped
        found_skipped = False
        for call in mock_r.hset.call_args_list:
            # call.args: (name, key, value) or (name, mapping={...})
            kwargs = call.kwargs
            if "mapping" in kwargs:
                mapping = kwargs["mapping"]
                if mapping.get("status") == "skipped":
                    if "members-only" in mapping.get("error", ""):
                        found_skipped = True
                        break
        
        self.assertTrue(found_skipped, "Did not find status='skipped' update in Redis calls")

if __name__ == "__main__":
    unittest.main()
