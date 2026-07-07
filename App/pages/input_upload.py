from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import streamlit as st

from App.auth import ROLE_ADMIN, ROLE_QA_ENGINEER, ROLE_RELEASE_ENGINEER, current_role
from App.workspace import BASE_DIR, DEFAULT_TEAM_LABEL, active_release_line, active_team_name, list_teams


def save_uploaded_release_inputs(team_name: str, release_line: str, files: dict[str, bytes]) -> tuple[bool, str, list[Path]]:
    team = re.sub(r"[^A-Za-z0-9._-]+", "-", str(team_name or "").strip().replace("\\", "-").replace("/", "-")).strip(".-_")
    release = re.sub(r"[^A-Za-z0-9._-]+", "-", str(release_line or "").strip().replace("\\", "-").replace("/", "-")).strip(".-_")
    if not team or team == DEFAULT_TEAM_LABEL:
        return False, "Enter a team name such as SourceOne, DPS, Avamar, or PackageTeam.", []
    if not release:
        return False, "Enter a concrete product version or release line such as 7.2.11.", []
    if "software.yml" not in files:
        return False, "software.yml is required before a release can be used by workflows.", []

    target_root = BASE_DIR / "Input" / "teams" / team / "releases" / release
    target_root.mkdir(parents=True, exist_ok=True)
    saved_paths: list[Path] = []
    for filename, content in files.items():
        if filename not in {"software.yml", "sample_version.pdf", "testcaseRepository.xlsx"}:
            continue
        target = target_root / filename
        target.write_bytes(content)
        saved_paths.append(target)

    st.session_state["active_team"] = team
    st.session_state["active_release_line"] = release
    return True, f"Input files saved for {team} / {release}.", saved_paths


def render_input_upload(ctx: Any, embedded: bool = False) -> None:
    if current_role() not in {ROLE_ADMIN, ROLE_RELEASE_ENGINEER, ROLE_QA_ENGINEER}:
        ctx.render_access_denied("Administrator, Release Engineer, or QA Engineer")
        return

    if not embedded:
        ctx.section_title("Input Upload", "Upload release input files for a team and product version.")
    else:
        st.markdown("**Upload release input files for a team and product version.**")
    upload_status = st.session_state.pop("input_upload_status", None)
    if upload_status:
        st.success(upload_status)

    known_teams = list_teams()
    team_options = sorted(set(known_teams + ["Avamar", "DPS", "PackageTeam", "SourceOne"]))
    form_col, _ = st.columns([0.68, 0.32])
    with form_col.form("release_input_upload_form"):
        top_cols = st.columns([1.25, 0.75])
        with top_cols[0]:
            try:
                team = st.selectbox(
                    "Team / Product Stream",
                    team_options,
                    index=team_options.index(active_team_name()) if active_team_name() in team_options else 0,
                    accept_new_options=True,
                    placeholder="Select or type team",
                )
            except TypeError:
                team = st.text_input("Team / Product Stream", value=active_team_name(), placeholder="Select existing or type new team")
        with top_cols[1]:
            release_line = st.text_input("Release", value=active_release_line(str(team)), placeholder="7.2.11")
        upload_cols = st.columns([1.45, 0.55])
        with upload_cols[0]:
            uploaded_files = st.file_uploader(
                "Files",
                type=["yml", "yaml", "pdf", "xlsx"],
                accept_multiple_files=True,
                help="software.yml is mandatory. Optional files: sample_version.pdf and testcaseRepository.xlsx.",
            )
        with upload_cols[1]:
            st.caption("Required: software.yml")
            submitted = st.form_submit_button("Save", type="primary", use_container_width=True)

    if submitted:
        selected_team = str(team or "").strip()
        files: dict[str, bytes] = {}
        for uploaded_file in uploaded_files or []:
            filename = uploaded_file.name
            if filename in {"software.yml", "software.yaml"}:
                files["software.yml"] = uploaded_file.getvalue()
            elif filename == "sample_version.pdf":
                files["sample_version.pdf"] = uploaded_file.getvalue()
            elif filename == "testcaseRepository.xlsx":
                files["testcaseRepository.xlsx"] = uploaded_file.getvalue()

        success, message, saved_paths = save_uploaded_release_inputs(selected_team, release_line, files)
        if success:
            saved_list = ", ".join(path.relative_to(BASE_DIR).as_posix() for path in saved_paths)
            st.session_state["input_upload_status"] = f"{message} Saved files: {saved_list}."
            st.rerun()
        else:
            st.error(message)
