import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import memory


class AgentMemoryTests(unittest.TestCase):
    def test_sqlite_memory_records_history_baseline_and_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "agent_memory.db"
            with patch.object(memory, "DB_PATH", db_path), patch("agent.memory.using_postgres", return_value=False):
                memory.init_db()
                memory.save_run_result(
                    run_id="run-1",
                    software="OpenSSL",
                    category="ALL",
                    build_ver="3.0.1",
                    cu_ver="3.0.0",
                    source="live server",
                    needs_update=True,
                )
                memory.log_failure("OpenSSL", "server-1", "timeout")

                history = memory.get_run_history("OpenSSL")
                baseline = memory.get_baseline("OpenSSL")
                failures = memory.get_recent_failures("OpenSSL")

            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["build_ver"], "3.0.1")
            self.assertTrue(history[0]["needs_update"])
            self.assertEqual(baseline["cu_ver"], "3.0.0")
            self.assertEqual(failures[0]["reason"], "timeout")


if __name__ == "__main__":
    unittest.main()
