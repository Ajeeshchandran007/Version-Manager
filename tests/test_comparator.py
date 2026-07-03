import unittest

from tests.conftest_path import PROJECT_ROOT  # noqa: F401
from Core.comparator import compare
from Core.notifier import is_actionable_update
from Core.workspace_assessment import blocker_reason, version_gap


class ComparatorTests(unittest.TestCase):
    def test_unknown_versions_are_not_up_to_date(self):
        report = compare(
            {"Example": {"Build Version": None, "Cumulative Update (CU)": None}},
            {"Example": {"Build Version": None, "Cumulative Update (CU)": None}},
        )

        self.assertTrue(report["Example"]["unknown"])
        self.assertFalse(report["Example"]["needs_update"])
        self.assertFalse(report["Example"]["build_match"])
        self.assertFalse(report["Example"]["cu_match"])

    def test_known_mismatch_requires_update(self):
        report = compare(
            {"Example": {"Build Version": "2.0.0", "Cumulative Update (CU)": None}},
            {"Example": {"Build Version": "1.0.0", "Cumulative Update (CU)": None}},
        )

        self.assertFalse(report["Example"]["unknown"])
        self.assertTrue(report["Example"]["needs_update"])

    def test_matching_cu_does_not_hide_build_gap(self):
        report = compare(
            {"SQL Server 2019": {"Build Version": "15.0.4470.1", "Cumulative Update (CU)": "CU32"}},
            {"SQL Server 2019": {"Build Version": "15.0.4385.2", "Cumulative Update (CU)": "CU32"}},
        )

        self.assertTrue(report["SQL Server 2019"]["needs_update"])
        self.assertTrue(is_actionable_update(report["SQL Server 2019"]))

    def test_exchange_short_and_long_formats_compare_equal(self):
        report = compare(
            {"MS Exchange Server 2019": {"Build Version": "15.2.858.10", "Cumulative Update (CU)": None}},
            {"MS Exchange Server 2019": {"Build Version": "15.02.0858.010", "Cumulative Update (CU)": None}},
        )

        self.assertTrue(report["MS Exchange Server 2019"]["build_match"])
        self.assertFalse(report["MS Exchange Server 2019"]["needs_update"])

    def test_mixed_vendor_version_scheme_requires_source_review(self):
        gap = version_gap("15.02.0858.010", "1748.037")
        readiness, reason = blocker_reason(
            "MS Exchange Server 2019",
            "15.02.0858.010",
            "1748.037",
            "",
            "CU15",
            gap,
            "LOW",
            has_target=True,
            needs_update=True,
        )

        self.assertEqual(gap, "Source Review")
        self.assertEqual(readiness, "Dependency Review Required")
        self.assertIn("different version schemes", reason)


if __name__ == "__main__":
    unittest.main()
