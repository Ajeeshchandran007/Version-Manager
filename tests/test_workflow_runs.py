import tempfile
import unittest
from pathlib import Path

from App.workflow_runs import list_workflow_runs, record_workflow_run


class WorkflowRunsTests(unittest.TestCase):
    def test_records_and_lists_team_release_run_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "app_state.db"
            record_workflow_run(
                db_path,
                run_id="run-1",
                team="SourceOne",
                release_line="7.2.11",
                workflow_scope="qa",
                category="ALL",
                status="completed",
                triggered_by="qauser",
                triggered_by_role="QA Engineer",
                started_at="2026-07-03T01:00:00",
                ended_at="2026-07-03T01:00:03",
                duration_seconds=3.1,
                total=5,
                needs_update_count=2,
                unknown_count=1,
                email_sent=True,
                summary={"trace_id": "pipeline-1"},
            )

            rows = list_workflow_runs(db_path, team="SourceOne", release_line="7.2.11")

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["run_id"], "run-1")
            self.assertEqual(rows[0]["workflow_scope"], "qa")
            self.assertEqual(rows[0]["triggered_by"], "qauser")
            self.assertTrue(rows[0]["email_sent"])
            self.assertEqual(rows[0]["summary"]["trace_id"], "pipeline-1")

    def test_upserts_same_run_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "app_state.db"
            base = {
                "db_path": db_path,
                "run_id": "run-1",
                "team": "SourceOne",
                "release_line": "7.2.11",
                "workflow_scope": "package",
                "category": "ALL",
                "triggered_by": "release",
                "triggered_by_role": "Release Engineer",
                "started_at": "2026-07-03T01:00:00",
                "ended_at": "2026-07-03T01:00:01",
            }
            record_workflow_run(**base, status="failed", error_message="first", summary={"error": "first"})
            record_workflow_run(**base, status="completed", total=4, summary={"total": 4})

            rows = list_workflow_runs(db_path)

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "completed")
            self.assertEqual(rows[0]["total"], 4)
