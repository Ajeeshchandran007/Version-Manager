import unittest
from unittest.mock import AsyncMock, patch

from tests.conftest_path import PROJECT_ROOT  # noqa: F401
from Core.version_fetcher import _fetch_authoritative_latest, _fill_cu_from_search_text


class VersionFetcherTests(unittest.IsolatedAsyncioTestCase):
    def test_repairs_sql_cu_from_gdr_table_row(self):
        parsed = {"Build Version": "15.0.4470.1", "Cumulative Update (CU)": None}
        source = "CU32 + GDR 15.0.4470.1 2019.150.4470.1 KB5090407 May 12, 2026"

        _fill_cu_from_search_text(parsed, source)

        self.assertEqual(parsed["Cumulative Update (CU)"], "CU32")

    def test_does_not_overwrite_existing_cu(self):
        parsed = {"Build Version": "15.0.4470.1", "Cumulative Update (CU)": "CU31"}

        _fill_cu_from_search_text(parsed, "CU32 + GDR 15.0.4470.1")

        self.assertEqual(parsed["Cumulative Update (CU)"], "CU31")

    async def test_authoritative_sql_lookup_prefers_latest_gdr_build(self):
        html = """
        CU32 + GDR 15.0.4470.1 2019.150.4470.1 May 12, 2026
        CU32 + GDR 15.0.4465.1 2019.150.4465.1 April 14, 2026
        CU32 (Latest) 15.0.4430.1 2019.150.4430.1 February 27, 2025
        """
        response = AsyncMock()
        response.text = html
        response.raise_for_status = lambda: None
        client = AsyncMock()
        client.get.return_value = response

        with patch("httpx.AsyncClient") as async_client:
            async_client.return_value.__aenter__.return_value = client
            result = await _fetch_authoritative_latest("SQL Server 2019")

        self.assertEqual(result["Build Version"], "15.0.4470.1")
        self.assertEqual(result["Cumulative Update (CU)"], "CU32")


if __name__ == "__main__":
    unittest.main()
