from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from App import server_config


class ServerConfigTests(unittest.TestCase):
    def test_release_scoped_yaml_wins_over_global_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            release_root = root / "Input" / "teams" / "SourceOne" / "releases" / "7.2.11"
            release_root.mkdir(parents=True)
            (release_root / "servers.yml").write_text(
                """
servers:
  OpenSSL:
    host: release-host
    method: ssh
    user: svc
    command: openssl version
""",
                encoding="utf-8",
            )
            (root / "Input").mkdir(exist_ok=True)
            (root / "Input" / "servers.yml").write_text(
                """
servers:
  OpenSSL:
    host: global-host
""",
                encoding="utf-8",
            )

            with patch.object(server_config, "BASE_DIR", root):
                configs = server_config.load_server_configs(
                    {},
                    team="SourceOne",
                    release_line="7.2.11",
                )

            self.assertEqual(configs["OpenSSL"]["host"], "release-host")

    def test_infers_team_release_from_config_path_and_expands_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            release_root = root / "Input" / "teams" / "DPS" / "releases" / "2.0"
            release_root.mkdir(parents=True)
            (release_root / "servers.yml").write_text(
                """
SQL Server 2019:
  host: ${SERVER_HOST}
  method: ssh
""",
                encoding="utf-8",
            )
            config = {"input_files": {"software_yml": "Input/teams/DPS/releases/2.0/software.yml"}}

            with patch.object(server_config, "BASE_DIR", root), patch.dict(os.environ, {"SERVER_HOST": "10.1.2.3"}):
                configs = server_config.load_server_configs(config)

            self.assertEqual(configs["SQL Server 2019"]["host"], "10.1.2.3")

    def test_missing_yaml_ignores_legacy_servers_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Input").mkdir()
            config = {"servers": {"OpenSSL": {"host": "legacy-host"}}}

            with patch.object(server_config, "BASE_DIR", root):
                configs = server_config.load_server_configs(config, team="SourceOne", release_line="7.2.11")

            self.assertEqual(configs, {})


if __name__ == "__main__":
    unittest.main()
