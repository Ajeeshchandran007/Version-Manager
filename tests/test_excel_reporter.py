import tempfile
import unittest
from pathlib import Path

import pandas as pd

from tests.conftest_path import PROJECT_ROOT  # noqa: F401
from Core.excel_reporter import EXCEL_COLUMNS, generate_excel_report


class ExcelReporterTests(unittest.TestCase):
    def test_generates_expected_columns(self):
        comparison = {
            "SQL Server 2019": {
                "current": {"Build Version": "15.0.4043.16", "Cumulative Update (CU)": "CU18"},
                "latest": {"Build Version": "15.0.4470.1", "Cumulative Update (CU)": "CU32"},
                "needs_update": True,
            }
        }
        vulnerabilities = {
            "SQL Server 2019": {
                "risk_level": "HIGH",
                "cves": [{"id": "CVE-2099-0001"}],
            }
        }

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "Software_Version_Assessment.xlsx"
            generate_excel_report(comparison, vulnerabilities, str(output))
            df = pd.read_excel(output)

        self.assertEqual(list(df.columns), EXCEL_COLUMNS)
        self.assertEqual(df.iloc[0]["Vendor"], "Microsoft")
        self.assertEqual(df.iloc[0]["Need Update (Yes/No)"], "Yes")
        self.assertEqual(df.iloc[0]["Version Status"], "Outdated")
        self.assertEqual(df.iloc[0]["Highest CVE Severity"], "No CVEs")


if __name__ == "__main__":
    unittest.main()
