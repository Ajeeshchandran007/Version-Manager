from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Any

import yaml

from App.workspace import (
    BASE_DIR,
    DEFAULT_TEAM_LABEL,
    WORKING_RELEASE_LABEL,
    safe_path_name,
    team_name_to_path_name,
)
from Utils.utils import load_config, logger

SERVER_CONFIG_FILENAMES = ("servers.yml", "servers.yaml")


def load_server_configs(
    config: dict[str, Any] | None = None,
    *,
    team: str | None = None,
    release_line: str | None = None,
    allow_legacy_config_fallback: bool = True,
) -> dict[str, dict[str, Any]]:
    """Load SSH/HTTP server inventory from release, team, or global YAML."""
    config = config or load_config()
    resolved_team, resolved_release = _resolve_context(config, team, release_line)

    for path in _candidate_paths(resolved_team, resolved_release):
        payload = _read_yaml(path)
        if payload:
            logger.info("Loaded server configuration from %s", path)
            return payload

    if allow_legacy_config_fallback and config.get("servers"):
        logger.warning(
            "Using deprecated config.json servers block. Move server inventory to "
            "Input/teams/<team>/releases/<release>/servers.yml."
        )
        return _normalize_server_payload(config.get("servers", {}))

    logger.info("No server configuration YAML found for team=%s release=%s.", resolved_team, resolved_release)
    return {}


def _resolve_context(
    config: dict[str, Any],
    team: str | None,
    release_line: str | None,
) -> tuple[str | None, str | None]:
    if team is not None or release_line is not None:
        return team, release_line

    software_path = str(config.get("input_files", {}).get("software_yml", ""))
    if not software_path:
        return None, None

    path = Path(software_path)
    parts = list(path.parts)
    try:
        teams_index = parts.index("teams")
        releases_index = parts.index("releases")
    except ValueError:
        return None, None

    if releases_index > teams_index + 1 and len(parts) > releases_index + 1:
        return parts[teams_index + 1], parts[releases_index + 1]
    return None, None


def _candidate_paths(team: str | None, release_line: str | None) -> list[Path]:
    paths: list[Path] = []
    if team and release_line and release_line != WORKING_RELEASE_LABEL:
        if team == DEFAULT_TEAM_LABEL:
            release_root = BASE_DIR / "Input" / "releases" / safe_path_name(release_line)
        else:
            release_root = BASE_DIR / "Input" / "teams" / team_name_to_path_name(team) / "releases" / safe_path_name(release_line)
        paths.extend(release_root / filename for filename in SERVER_CONFIG_FILENAMES)

    if team and team != DEFAULT_TEAM_LABEL:
        team_root = BASE_DIR / "Input" / "teams" / team_name_to_path_name(team)
        paths.extend(team_root / filename for filename in SERVER_CONFIG_FILENAMES)

    input_root = BASE_DIR / "Input"
    paths.extend(input_root / filename for filename in SERVER_CONFIG_FILENAMES)
    return paths


def _read_yaml(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            payload = yaml.safe_load(handle) or {}
    except Exception as exc:
        logger.error("Failed to read server configuration %s: %s", path, exc)
        return {}
    return _normalize_server_payload(payload)


def _normalize_server_payload(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        return {}
    servers = payload.get("servers", payload)
    if not isinstance(servers, dict):
        return {}
    expanded = _expand_env_placeholders(copy.deepcopy(servers))
    return {str(name): cfg for name, cfg in expanded.items() if isinstance(cfg, dict)}


def _expand_env_placeholders(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _expand_env_placeholders(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env_placeholders(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"\$\{([^}]+)\}", lambda match: os.environ.get(match.group(1), match.group(0)), value)
    return value
