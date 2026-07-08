import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.conftest_path import PROJECT_ROOT  # noqa: F401


class StreamlitScheduleTests(unittest.TestCase):
    def test_save_schedule_creates_runtime_config_when_config_json_missing(self):
        import streamlit_app

        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            with patch("streamlit_app.CONFIG_FILE", config_path), patch(
                "streamlit_app.load_config", return_value={"default_category": "ALL", "schedule_cron": "0 9 * * 4"}
            ), patch("streamlit_app.clear_dashboard_cache"):
                config = streamlit_app.save_schedule_config("0 9 * * *")
            exists = config_path.exists()

        self.assertEqual(config["schedule_cron"], "0 9 * * *")
        self.assertTrue(exists)


if __name__ == "__main__":
    unittest.main()
