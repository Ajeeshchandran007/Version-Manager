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
WORKING_RELEASE_LABEL = "Working / Latest"
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
        for path in teams_dir.iterdir():
            if not path.is_dir():
                continue
            has_working_input = (path / "software.yml").exists()
            releases_dir = path / "releases"
            has_release_input = releases_dir.exists() and any(
                release_path.is_dir() and (release_path / "software.yml").exists()
                for release_path in releases_dir.iterdir()
            )
            if has_working_input or has_release_input:
                teams.add(path.name)
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


def list_release_lines(team: str | None = None) -> list[str]:
    team = team or active_team_name()
    releases: list[str] = []
    releases_dir = team_input_release_root(team)
    if releases_dir.exists():
        releases.extend(
            path.name
            for path in releases_dir.iterdir()
            if path.is_dir() and (path / "software.yml").exists()
        )
    if releases:
        return sorted(releases)
    return []


def active_release_line(team: str | None = None) -> str:
    team = team or active_team_name()
    release = st.session_state.get("active_release_line", "")
    releases = list_release_lines(team)
    if release in releases:
        return release
    return releases[0] if releases else ""


def team_input_file_path(team: str, filename: str, release_line: str | None = None) -> Path:
    release_line = release_line or WORKING_RELEASE_LABEL
    if release_line != WORKING_RELEASE_LABEL:
        return team_input_release_path(team, release_line) / filename
    if team == DEFAULT_TEAM_LABEL:
        return INPUT_DIR / filename
    return INPUT_DIR / "teams" / team_name_to_path_name(team) / filename


def team_input_software_path(team: str, release_line: str | None = None) -> Path:
    return team_input_file_path(team, "software.yml", release_line)


def team_input_release_root(team: str) -> Path:
    if team == DEFAULT_TEAM_LABEL:
        return INPUT_DIR / "releases"
    return INPUT_DIR / "teams" / team_name_to_path_name(team) / "releases"


def team_input_release_path(team: str, release_line: str) -> Path:
    return team_input_release_root(team) / safe_path_name(release_line)


def relpath(path: Path) -> str:
    return path.relative_to(BASE_DIR).as_posix()


def team_workspace_output_dir(team: str | None = None, release_line: str | None = None) -> Path:
    team = team or active_team_name()
    release_line = release_line or active_release_line(team)
    if release_line and release_line != WORKING_RELEASE_LABEL:
        if team == DEFAULT_TEAM_LABEL:
            return WORKSPACES_DIR / DEFAULT_TEAM_LABEL / "releases" / safe_path_name(release_line) / "output"
        return WORKSPACES_DIR / team_name_to_path_name(team) / "releases" / safe_path_name(release_line) / "output"
    if team == DEFAULT_TEAM_LABEL:
        return OUTPUT_DIR
    return WORKSPACES_DIR / team_name_to_path_name(team) / "output"


def active_output_path(filename: str) -> Path:
    team = active_team_name()
    release_line = active_release_line(team)
    return team_workspace_output_dir(team, release_line) / filename


def active_config(config: dict[str, Any]) -> dict[str, Any]:
    team = active_team_name()
    release_line = active_release_line(team)
    if not release_line:
        scoped = json.loads(json.dumps(config))
        output_files = scoped.setdefault("output_files", {})
        output_root = team_workspace_output_dir(team, "")
        for key, filename in RELEASE_OUTPUT_KEYS.items():
            output_files[key] = relpath(output_root / filename)
        return scoped
    return scoped_config_for_context(config, team, release_line)


def scoped_config_for_context(config: dict[str, Any], team: str, release_line: str) -> dict[str, Any]:
    scoped = json.loads(json.dumps(config))
    input_files = scoped.setdefault("input_files", {})
    if not release_line:
        raise FileNotFoundError(
            f"No product release input found for {team}. Expected Input/teams/{team}/releases/<release>/software.yml."
        )
    input_root = team_input_software_path(team, release_line).parent
    for key, filename in TEAM_INPUT_FILES.items():
        input_files[key] = relpath(input_root / filename)
    output_files = scoped.setdefault("output_files", {})
    output_root = team_workspace_output_dir(team, release_line)
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


def create_release_line_snapshot(team_name: str, release_line: str, base_release_line: str | None = None) -> tuple[bool, str]:
    team = active_team_name() if not team_name else team_name
    release = safe_path_name(release_line)
    if not release:
        return False, "Enter a product version or release line such as 7.2.11."
    if release == safe_path_name(WORKING_RELEASE_LABEL):
        return False, "Use a concrete product version such as 7.2.11."

    target_root = team_input_release_path(team, release)
    if (target_root / "software.yml").exists():
        return False, f"Product version {release} already exists for {team}."

    source_release = base_release_line or active_release_line(team)
    source_root = team_input_software_path(team, source_release).parent
    if not (source_root / "software.yml").exists():
        return False, f"Base software.yml was not found: {source_root / 'software.yml'}"

    target_root.mkdir(parents=True, exist_ok=True)
    for item in source_root.iterdir():
        if item.name == "releases":
            continue
        destination = target_root / item.name
        if item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(item, destination)

    st.session_state["active_release_line"] = release
    return True, f"Product version {release} created for {team}."
