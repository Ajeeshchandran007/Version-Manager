import unittest

from tests.conftest_path import PROJECT_ROOT  # noqa: F401
from Core.notifier import build_html_report, build_report, count_actionable_updates


class NotifierTests(unittest.TestCase):
    def test_unknown_status_is_reported_separately(self):
        body = build_report({
            "Example": {
                "latest": {"Build Version": None, "Cumulative Update (CU)": None},
                "current": {"Build Version": None, "Cumulative Update (CU)": None},
                "current_source": "unknown",
                "build_match": False,
                "cu_match": False,
                "unknown": True,
                "needs_update": False,
            }
        })

        self.assertIn("UNKNOWN VERSION STATUS", body)
        self.assertIn("Software Version & Security Assessment Report", body)
        self.assertNotIn("Up to date (1)", body)

    def test_report_hides_not_applicable_cu_noise(self):
        body = build_report({
            "Example": {
                "latest": {"Build Version": "2.0.0", "Cumulative Update (CU)": None},
                "current": {"Build Version": "1.0.0", "Cumulative Update (CU)": None},
                "current_source": "live server",
                "build_match": False,
                "cu_match": False,
                "unknown": False,
                "needs_update": True,
            }
        }, {
            "Example": {
                "risk_level": "LOW",
                "severity": "NONE",
                "cves": [],
                "critical_cves": [],
            }
        })

        self.assertIn("EXECUTIVE SUMMARY", body)
        self.assertIn("PRIORITY UPDATE REQUIRED", body)
        self.assertIn("SECURITY RISK SUMMARY", body)
        self.assertNotIn("current=None", body)
        self.assertNotIn("latest=None", body)

    def test_html_report_has_styled_sections(self):
        html = build_html_report({
            "Example": {
                "latest": {"Build Version": "2.0.0", "Cumulative Update (CU)": None},
                "current": {"Build Version": "1.0.0", "Cumulative Update (CU)": None},
                "current_source": "live server",
                "build_match": False,
                "cu_match": False,
                "unknown": False,
                "needs_update": True,
            }
        }, {
            "Example": {
                "risk_level": "LOW",
                "severity": "NONE",
                "cves": [],
                "critical_cves": [],
            }
        })

        self.assertIn("<html>", html)
        self.assertIn("Executive Summary", html)
        self.assertIn("Priority Update Required", html)
        self.assertIn("<table", html)
        self.assertIn("Software Version &amp; Security Assessment Report", html)
        self.assertIn('role="presentation"', html)

    def test_html_report_does_not_show_gap_for_matching_versions(self):
        comparison = {
            "VLC Media Player": {
                "latest": {"Build Version": "3.0.23", "Cumulative Update (CU)": None},
                "current": {"Build Version": "3.0.23", "Cumulative Update (CU)": None},
                "current_source": "sample pdf",
                "build_match": True,
                "cu_match": True,
                "unknown": False,
                "needs_update": True,
            },
            "Adobe Digital Editions": {
                "latest": {"Build Version": "4.5.12", "Cumulative Update (CU)": None},
                "current": {"Build Version": "4.5.12", "Cumulative Update (CU)": None},
                "current_source": "sample pdf",
                "build_match": True,
                "cu_match": True,
                "unknown": False,
                "needs_update": True,
            },
            "Real Update": {
                "latest": {"Build Version": "2.0.0", "Cumulative Update (CU)": None},
                "current": {"Build Version": "1.0.0", "Cumulative Update (CU)": None},
                "current_source": "sample pdf",
                "build_match": False,
                "cu_match": True,
                "unknown": False,
                "needs_update": True,
            },
        }
        html = build_html_report(comparison)

        self.assertEqual(count_actionable_updates(comparison), 1)
        self.assertIn("1 application(s) are behind latest vendor releases.", html)
        self.assertNotIn("Build/version gap", html)
        self.assertNotIn("VLC Media Player</td>", html)
        self.assertNotIn("Adobe Digital Editions</td>", html)

    def test_html_report_shows_no_updates_message(self):
        html = build_html_report({
            "Example": {
                "latest": {"Build Version": "2.0.0", "Cumulative Update (CU)": None},
                "current": {"Build Version": "2.0.0", "Cumulative Update (CU)": None},
                "current_source": "live server",
                "build_match": True,
                "cu_match": True,
                "unknown": False,
                "needs_update": False,
            }
        })

        self.assertIn("No updates required.", html)


if __name__ == "__main__":
    unittest.main()
