from __future__ import annotations

from typing import Any

import streamlit as st

from App.auth import (
    ROLE_ADMIN,
    ROLE_QA_ENGINEER,
    ROLE_RELEASE_ENGINEER,
)
from App.workspace import (
    DEFAULT_TEAM_LABEL,
    WORKING_RELEASE_LABEL,
    active_release_line,
    active_team_name,
    allowed_teams_for_user,
    list_release_lines,
    safe_path_name,
)


ACTION_ROLES = {ROLE_ADMIN, ROLE_RELEASE_ENGINEER, ROLE_QA_ENGINEER}
ADMIN_ROLES = {ROLE_ADMIN}
RISK_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE", "UNKNOWN"]
PRIMARY_CACHE_NAMESPACES = {"software_versions", "vulnerabilities", "nvd"}
CACHE_NAMESPACE_LABELS = {
    "software_versions": "Software Versions",
    "vulnerabilities": "Vulnerability Assessments",
    "nvd": "NVD CVE Lookup",
    "tavily": "Tavily Search",
    "openai_analysis": "OpenAI Analysis",
    "vendor_sources": "Vendor Sources",
}

def render_context_selector(ctx: Any, location: str = "dashboard") -> dict[str, Any]:
    teams = allowed_teams_for_user()
    current_team = active_team_name()
    if len(teams) == 1:
        st.text_input("Team / Product Stream", value=teams[0], disabled=True, key=f"{location}_team_locked")
        selected_team = teams[0]
    else:
        team_key = f"{location}_team_selector"
        if st.session_state.get(team_key) not in teams:
            st.session_state[team_key] = current_team
        selected_team = st.selectbox(
            "Team / Product Stream",
            teams,
            index=teams.index(current_team) if current_team in teams else 0,
            key=team_key,
        )

    if selected_team != st.session_state.get("active_team", DEFAULT_TEAM_LABEL):
        st.session_state["active_team"] = selected_team
        st.session_state.pop("active_release_line", None)
        ctx.clear_dashboard_cache()
        st.rerun()

    releases = list_release_lines(selected_team)
    if not releases:
        st.error(
            f"No product release input found for {selected_team}. Add a release-specific software.yml under "
            f"Input/teams/{selected_team}/releases/<release>/software.yml before running workflows."
        )
        return {"ready": False, "team": selected_team, "release": ""}
    current_release = active_release_line(selected_team)
    release_key = f"{location}_{safe_path_name(selected_team)}_release_line_selector"
    if st.session_state.get(release_key) not in releases:
        st.session_state[release_key] = current_release
    selected_release = st.selectbox(
        "Product Version / Release Line",
        releases,
        index=releases.index(current_release) if current_release in releases else 0,
        key=release_key,
    )
    if selected_release != st.session_state.get("active_release_line", WORKING_RELEASE_LABEL):
        st.session_state["active_release_line"] = selected_release
        ctx.clear_dashboard_cache()
        st.rerun()
    return {"ready": True, "team": selected_team, "release": selected_release}
