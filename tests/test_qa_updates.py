import json
import tempfile
import unittest
from pathlib import Path

from tests.conftest_path import PROJECT_ROOT  # noqa: F401
from App.qa_updates import QAUpdateConflict, build_qa_update_payload, save_qa_row_update


class QAUpdatesTests(unittest.TestCase):
    def test_row_update_increments_revision_without_removing_other_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            qa_file = Path(tmp) / "qa_validation.json"
            db_path = Path(tmp) / "app_state.db"
            qa_file.write_text(json.dumps({
                "App A": {"Software Name": "App A", "QA Revision": 0},
                "App B": {"Software Name": "App B", "QA Revision": 0},
            }))

            payload = build_qa_update_payload(
                "Installed Successfully",
                "PASS",
                2,
                2,
                0,
                0,
                "ok",
                "2026-07-02",
            )
            save_qa_row_update(
                qa_file,
                "App A",
                payload,
                expected_revision=0,
                updated_by="qa.user",
                db_path=db_path,
                team="SourceOne",
                release_line="7.2.11",
            )

            data = json.loads(qa_file.read_text())
            self.assertEqual(data["App A"]["QA Revision"], 1)
            self.assertEqual(data["App A"]["Test Result"], "PASS")
            self.assertIn("App B", data)

    def test_stale_revision_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            qa_file = Path(tmp) / "qa_validation.json"
            db_path = Path(tmp) / "app_state.db"
            qa_file.write_text(json.dumps({"App A": {"Software Name": "App A", "QA Revision": 2}}))

            with self.assertRaises(QAUpdateConflict):
                save_qa_row_update(
                    qa_file,
                    "App A",
                    {"Test Result": "PASS"},
                    expected_revision=1,
                    updated_by="qa.user",
                    db_path=db_path,
                    team="SourceOne",
                    release_line="7.2.11",
                )


if __name__ == "__main__":
    unittest.main()
