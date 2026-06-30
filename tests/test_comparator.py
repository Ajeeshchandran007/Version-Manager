import unittest

from tests.conftest_path import PROJECT_ROOT  # noqa: F401
from Core.comparator import compare


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


if __name__ == "__main__":
    unittest.main()

