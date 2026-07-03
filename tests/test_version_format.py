import unittest

from tests.conftest_path import PROJECT_ROOT  # noqa: F401
from Utils.version_format import canonical_version


class VersionFormatTests(unittest.TestCase):
    def test_exchange_short_version_uses_long_exsetup_format(self):
        self.assertEqual(
            canonical_version("MS Exchange Server 2019", "15.2.858.10"),
            "15.02.0858.010",
        )

    def test_exchange_long_version_is_stable(self):
        self.assertEqual(
            canonical_version("MS Exchange Server 2019", "15.02.1748.046"),
            "15.02.1748.046",
        )

    def test_exchange_partial_build_gets_exchange_prefix(self):
        self.assertEqual(
            canonical_version("MS Exchange Server 2019", "1748.037"),
            "15.02.1748.037",
        )

    def test_non_exchange_version_is_unchanged(self):
        self.assertEqual(canonical_version("OpenSSL", "4.0.1"), "4.0.1")


if __name__ == "__main__":
    unittest.main()
