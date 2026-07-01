import unittest

from tests.conftest_path import PROJECT_ROOT  # noqa: F401
from Core.comparator import compare
from Core.notifier import is_actionable_update


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


if __name__ == "__main__":
    unittest.main()
