import tempfile
import unittest
from pathlib import Path

from tests.conftest_path import PROJECT_ROOT  # noqa: F401
from App.workflow_locks import WorkflowAlreadyRunning, workflow_lock


class WorkflowLocksTests(unittest.TestCase):
    def test_same_team_release_lock_blocks_second_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "app_state.db"
            with workflow_lock(db_path, team="SourceOne", release="7.2.11", scope="workflow", owner="user1"):
                with self.assertRaises(WorkflowAlreadyRunning):
                    with workflow_lock(db_path, team="SourceOne", release="7.2.11", scope="workflow", owner="user2"):
                        pass

    def test_different_release_can_run_independently(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "app_state.db"
            with workflow_lock(db_path, team="SourceOne", release="7.2.11", scope="workflow", owner="user1"):
                with workflow_lock(db_path, team="SourceOne", release="7.2.12", scope="workflow", owner="user2"):
                    self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
