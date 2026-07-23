import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from tests.conftest_path import PROJECT_ROOT  # noqa: F401
from App.pages import (
    render_compatibility_check,
    render_comparison,
    render_dashboard,
    render_dashboard_page,
    render_inventory,
    render_latest,
    render_operations,
    render_package_readiness,
    render_reports,
)
from App.pages.admin import save_uploaded_release_inputs
from App.pages.qa_validation import can_perform_qa_signoff
from App.pages.security import render_vulnerabilities
from App.pages.support import visible_output_files_for_role
from App.scan_reports import load_parsed_scan_findings, parse_scan_report, save_parsed_scan_findings
from App.qa_history import build_qa_signoff_history_record, history_dataframe
from App.qa_signoff import build_qa_signoff, load_qa_signoff, save_qa_signoff
from App.navigation import pages_for_role
from App.assistant_chat import ROLE_ASSISTANT_PAGES
from App.auth import ROLE_ADMIN, ROLE_QA_ENGINEER, ROLE_RELEASE_ENGINEER
from App.data_loaders import inventory_source_label
from App.workspace import active_team_name


class PageRefactorTests(unittest.TestCase):
    def test_page_package_reexports_expected_renderers(self):
        self.assertTrue(callable(render_operations))
        self.assertTrue(callable(render_dashboard))
        self.assertTrue(callable(render_dashboard_page))
        self.assertTrue(callable(render_inventory))
        self.assertTrue(callable(render_latest))
        self.assertTrue(callable(render_comparison))
        self.assertTrue(callable(render_package_readiness))
        self.assertTrue(callable(render_compatibility_check))
        self.assertTrue(callable(render_reports))
        self.assertTrue(callable(render_vulnerabilities))

    def test_navigation_includes_qa_validation_for_qa_and_admin(self):
        base_pages = [
            "Dashboard",
            "Software Inventory",
            "Operations",
            "Latest Versions",
            "Version Comparison",
            "Compatibility Review",
            "Reports",
        ]
        common = {
            "base_pages": base_pages,
            "release_pages": ["Package Readiness"],
            "qa_pages": ["QA Validation"],
            "security_pages": ["Vulnerability Assessment"],
            "cache_pages": ["Cache Analytics"],
            "role_assistant_pages": ROLE_ASSISTANT_PAGES,
            "admin_pages": ["Audit Logs", "Admin User Management", "Settings"],
            "action_roles": {ROLE_ADMIN, ROLE_RELEASE_ENGINEER, ROLE_QA_ENGINEER},
            "workflow_monitor_page": "Workflow Monitor",
        }

        qa_pages = pages_for_role(ROLE_QA_ENGINEER, **common)
        admin_pages = pages_for_role(ROLE_ADMIN, **common)
        release_pages = pages_for_role(ROLE_RELEASE_ENGINEER, **common)

        self.assertIn("QA Validation", qa_pages)
        self.assertIn("QA Validation", admin_pages)
        self.assertNotIn("QA Validation", release_pages)
        self.assertIn("Vulnerability Assessment", admin_pages)
        self.assertIn("Vulnerability Assessment", release_pages)
        self.assertNotIn("Vulnerability Assessment", qa_pages)
        self.assertLess(qa_pages.index("QA Validation"), qa_pages.index("QA Assistant"))
        self.assertIn("Cache Analytics", qa_pages)
        self.assertIn("Cache Analytics", admin_pages)
        self.assertIn("Cache Analytics", release_pages)
        self.assertGreater(qa_pages.index("Cache Analytics"), qa_pages.index("Reports"))
        self.assertGreater(admin_pages.index("Cache Analytics"), admin_pages.index("Reports"))
        self.assertGreater(release_pages.index("Cache Analytics"), release_pages.index("Reports"))

    def test_active_team_defaults_to_first_alphabetical_team(self):
        with patch("App.workspace.allowed_teams_for_user", return_value=["Cisco", "DPS", "SourceOne"]):
            self.assertEqual(active_team_name(), "Cisco")

    def test_qa_signoff_save_load_and_history_record(self):
        qa_df = pd.DataFrame(
            [
                {
                    "Software Name": "App A",
                    "Test Result": "PASS",
                    "Test Case Count": 2,
                    "Test Cases Executed": 2,
                    "Test Cases Passed": 2,
                    "Test Cases Failed": 0,
                    "Test Cases Blocked / Not Tested": 0,
                },
                {
                    "Software Name": "App B",
                    "Test Result": "WARNING",
                    "Test Case Count": 1,
                    "Test Cases Executed": 0,
                    "Test Cases Passed": 0,
                    "Test Cases Failed": 0,
                    "Test Cases Blocked / Not Tested": 1,
                },
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            signoff = build_qa_signoff("SourceOne", "7.2.11", qa_df, "qa.user", "reviewed")
            save_qa_signoff(output_dir, signoff)

            loaded = load_qa_signoff(output_dir)
            history_record = build_qa_signoff_history_record(loaded, qa_df)
            history_df = history_dataframe([history_record])

        self.assertEqual(loaded["product"], "SourceOne")
        self.assertEqual(loaded["release_line"], "7.2.11")
        self.assertEqual(loaded["status"], "QA Signed Off With Warnings")
        self.assertEqual(history_df.iloc[0]["signed_by"], "qa.user")

    def test_input_upload_saves_release_files_under_team_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            files = {
                "software.yml": b"software:\n  App A: {}\n",
                "sample_version.pdf": b"%PDF-1.4",
                "testcaseRepository.xlsx": b"xlsx",
            }
            with patch("App.pages.input_upload.BASE_DIR", base_dir):
                ok, message, saved_paths = save_uploaded_release_inputs("Source One", "7.2.11", files)

            target_dir = base_dir / "Input" / "teams" / "Source-One" / "releases" / "7.2.11"
            self.assertTrue(ok, message)
            self.assertEqual({path.name for path in saved_paths}, set(files))
            self.assertTrue((target_dir / "software.yml").exists())
            self.assertTrue((target_dir / "sample_version.pdf").exists())
            self.assertTrue((target_dir / "testcaseRepository.xlsx").exists())

    def test_vulnerability_scan_parse_and_persist_json_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            scan_file = output_dir / "scan.json"
            scan_file.write_text(
                json.dumps(
                    {
                        "findings": [
                            {
                                "software": "OpenSSL",
                                "version": "3.0.1",
                                "cve": "CVE-2026-0001",
                                "severity": "high",
                                "scanner": "UnitScanner",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            findings = parse_scan_report(scan_file)
            save_parsed_scan_findings(output_dir, findings)
            loaded = load_parsed_scan_findings(output_dir)

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["Software Name"], "OpenSSL")
        self.assertEqual(loaded[0]["Risk Level"], "HIGH")
        self.assertEqual(loaded[0]["Scanner Source"], "UnitScanner")

    def test_support_helpers_are_decoupled_from_streamlit_app(self):
        with patch("App.pages.support.active_output_path", side_effect=lambda name: Path("output") / name):
            files = visible_output_files_for_role("Admin")

        labels = [label for label, _ in files]
        self.assertIn("Management Report - HTML", labels)
        self.assertIn("QA Validation Data", labels)

    def test_inventory_source_label_requires_active_server_config(self):
        self.assertEqual(
            inventory_source_label("OpenSSL", "live server", {"OpenSSL": {"host": "server"}}),
            "Configured Server",
        )
        self.assertEqual(inventory_source_label("OpenSSL", "live server", {}), "PDF Inventory")
        self.assertEqual(inventory_source_label("OpenSSL", "PDF fallback - server unreachable", {}), "PDF Inventory")

    def test_qa_signoff_permission_helper_uses_role_or_permission(self):
        with patch("App.pages.qa_validation.current_role", return_value="Admin"), patch(
            "App.pages.qa_validation.current_user", return_value={"permissions": []}
        ):
            self.assertTrue(can_perform_qa_signoff())

        with patch("App.pages.qa_validation.current_role", return_value="QA Engineer"), patch(
            "App.pages.qa_validation.current_user", return_value={"permissions": ["qa_signoff"]}
        ):
            self.assertTrue(can_perform_qa_signoff())

        with patch("App.pages.qa_validation.current_role", return_value="QA Engineer"), patch(
            "App.pages.qa_validation.current_user", return_value={"permissions": []}
        ):
            self.assertFalse(can_perform_qa_signoff())


if __name__ == "__main__":
    unittest.main()
