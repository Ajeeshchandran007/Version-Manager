import tempfile
import time
import unittest

from tests.conftest_path import PROJECT_ROOT  # noqa: F401
from Core.cache import JsonCacheBackend


class CacheTests(unittest.TestCase):
    def test_json_cache_hit_before_ttl_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = JsonCacheBackend(tmp)
            cache.set("software_versions", "sql", {"version": "1"}, "test", {"api_calls": 1})

            entry = cache.get("software_versions", "sql", ttl_seconds=60)

        self.assertIsNotNone(entry)
        self.assertEqual(entry["value"], {"version": "1"})
        self.assertEqual(entry["cache"]["status"], "hit")

    def test_json_cache_miss_after_ttl_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = JsonCacheBackend(tmp)
            cache.set("software_versions", "sql", {"version": "1"}, "test")
            time.sleep(1.1)

            entry = cache.get("software_versions", "sql", ttl_seconds=1)

        self.assertIsNone(entry)


if __name__ == "__main__":
    unittest.main()
