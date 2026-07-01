from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import streamlit as st

from App.auth import ROLE_ADMIN, current_role, user_team_scope
from Utils.utils import load_config

BASE_DIR = Path(__file__).resolve().parents[1]
INPUT_DIR = BASE_DIR / "Input"
OUTPUT_DIR = BASE_DIR / "output"
WORKSPACES_DIR = BASE_DIR / "workspaces"

DEFAULT_TEAM_LABEL = "Default"
RELEASE_OUTPUT_KEYS = {
    "latest_version_json": "latest_versions.json",
    "current_version_json": "current_versions.json",
    "comparison_report_json": "comparison_report.json",
    "vulnerability_report_json": "vulnerability_report.json",
    "package_readiness_json": "package_readiness.json",
    "qa_validation_json": "qa_validation.json",
    "testcase_impact_json": "testcase_impact.json",
    "testcase_impact_xlsx": "Test_Case_Impact_Assessment.xlsx",
    "excel_assessment": "Software_Version_Assessment.xlsx",
}
TEAM_INPUT_FILES = {
    "software_yml": "software.yml",
    "current_version_pdf": "sample_version.pdf",
    "testcase_repository_xlsx": "testcaseRepository.xlsx",
}


def project_path(config_path: str | Path) -> Path:
    path = Path(config_path)
    return path if path.is_absolute() else BASE_DIR / path


def safe_path_name(name: str) -> str:
    cleaned = str(name or "").strip()
    cleaned = cleaned.replace("\\", "-").replace("/", "-")
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", cleaned).strip(".-_")
    return cleaned


def team_name_to_path_name(name: str) -> str:
    return safe_path_name(name) or DEFAULT_TEAM_LABEL


def list_teams() -> list[str]:
    teams = set()
    teams_dir = INPUT_DIR / "teams"
    if teams_dir.exists():
        teams.update(
            path.name
            for path in teams_dir.iterdir()
            if path.is_dir() and (path / "software.yml").exists()
        )
    if not teams and (INPUT_DIR / "software.yml").exists():
        teams.add(DEFAULT_TEAM_LABEL)
    return sorted(teams)


def allowed_teams_for_user(user: dict[str, Any] | None = None) -> list[str]:
    teams = list_teams()
    scope = user_team_scope(user)
    if "*" in scope or current_role() == ROLE_ADMIN:
        return teams
    allowed = [team for team in teams if team in scope]
    return allowed or teams[:1]


def active_team_name() -> str:
    team = st.session_state.get("active_team", DEFAULT_TEAM_LABEL)
    teams = allowed_teams_for_user()
    if team in teams:
        return team
    return teams[0] if teams else DEFAULT_TEAM_LABEL


def team_input_file_path(team: str, filename: str) -> Path:
    if team == DEFAULT_TEAM_LABEL:
        return INPUT_DIR / filename
    return INPUT_DIR / "teams" / team_name_to_path_name(team) / filename


def team_input_software_path(team: str) -> Path:
    return team_input_file_path(team, "software.yml")


def relpath(path: Path) -> str:
    return path.relative_to(BASE_DIR).as_posix()


def team_workspace_output_dir(team: str | None = None) -> Path:
    team = team or active_team_name()
    if team == DEFAULT_TEAM_LABEL:
        return OUTPUT_DIR
    return WORKSPACES_DIR / team_name_to_path_name(team) / "output"


def active_output_path(filename: str) -> Path:
    team = active_team_name()
    return team_workspace_output_dir(team) / filename


def active_config(config: dict[str, Any]) -> dict[str, Any]:
    team = active_team_name()
    if team == DEFAULT_TEAM_LABEL:
        return config
    scoped = json.loads(json.dumps(config))
    input_files = scoped.setdefault("input_files", {})
    input_root = team_input_software_path(team).parent
    for key, filename in TEAM_INPUT_FILES.items():
        input_files[key] = relpath(input_root / filename)
    output_files = scoped.setdefault("output_files", {})
    output_root = active_output_path("__placeholder__").parent
    for key, filename in RELEASE_OUTPUT_KEYS.items():
        output_files[key] = relpath(output_root / filename)
    return scoped


def config_path_for_result(output_key: str) -> str:
    config = active_config(load_config())
    output_files = config.get("output_files", {})
    if output_key in output_files:
        return str(project_path(output_files[output_key]))
    filename = RELEASE_OUTPUT_KEYS.get(output_key, "")
    return str(active_output_path(filename)) if filename else ""


def create_team_snapshot(team_name: str, config: dict[str, Any]) -> tuple[bool, str]:
    team = team_name_to_path_name(team_name)
    if not team or team == DEFAULT_TEAM_LABEL:
        return False, "Enter a team name such as SourceOne, DPS, Avamar, or PackageTeam."
    target_root = team_input_software_path(team).parent
    target = target_root / "software.yml"
    if target.exists():
        return False, f"Team {team} already has an input software.yml."
    active_source_root = team_input_software_path(active_team_name()).parent
    fallback = project_path(config.get("input_files", {}).get("software_yml", "Input/teams/SourceOne/software.yml")).parent
    source_root = active_source_root if (active_source_root / "software.yml").exists() else fallback
    source = source_root / "software.yml"
    if not source.exists():
        return False, f"Base software.yml was not found: {source}"
    target_root.mkdir(parents=True, exist_ok=True)
    for item in source_root.iterdir():
        destination = target_root / item.name
        if item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(item, destination)
    st.session_state["active_team"] = team
    return True, f"Team {team} created from current software.yml."
