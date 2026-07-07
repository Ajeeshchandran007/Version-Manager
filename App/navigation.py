from __future__ import annotations

from typing import Any

import streamlit as st

from App.auth import ROLE_ADMIN, ROLE_QA_ENGINEER, ROLE_RELEASE_ENGINEER, clear_user_session, current_role, current_user
from App.workspace import active_release_line, active_team_name


def pages_for_role(
    role: str,
    *,
    base_pages: list[str],
    release_pages: list[str],
    qa_pages: list[str],
    role_assistant_pages: dict[str, str],
    admin_pages: list[str],
    action_roles: set[str],
    workflow_monitor_page: str,
) -> list[str]:
    pages = [*base_pages]
    if role not in action_roles and "Operations" in pages:
        pages.remove("Operations")
    if role in {ROLE_ADMIN, ROLE_RELEASE_ENGINEER}:
        insert_at = pages.index("Compatibility Check") if "Compatibility Check" in pages else len(pages)
        for page in reversed(release_pages):
            pages.insert(insert_at, page)
    if role in {ROLE_ADMIN, ROLE_QA_ENGINEER}:
        insert_at = pages.index("Reports") if "Reports" in pages else len(pages)
        for page in reversed(qa_pages):
            if page not in pages:
                pages.insert(insert_at, page)
    if role in {ROLE_ADMIN, ROLE_RELEASE_ENGINEER}:
        operations_index = pages.index("Operations") + 1 if "Operations" in pages else len(pages)
        pages.insert(operations_index, workflow_monitor_page)
    assistant_page = role_assistant_pages.get(role, "AI Assistant")
    if assistant_page not in pages:
        insert_at = pages.index("Reports") if "Reports" in pages else len(pages)
        pages.insert(insert_at, assistant_page)
    if role == ROLE_ADMIN:
        pages.extend(admin_pages)
    return pages


def render_sidebar(
    config: dict[str, Any],
    workflow_status: str,
    last_scan: str,
    *,
    pages: list[str],
    next_scan: str,
) -> str:
    with st.sidebar:
        user = current_user()
        active_team = active_team_name()
        active_release = active_release_line(active_team)
        st.markdown("### Version Manager")
        st.caption("Software posture and remediation operations")
        st.markdown(
            f"""
            <div class="vm-sidebar-card">
                <div class="vm-sidebar-kv">Signed In<strong>{user.get("display_name", user.get("username", "Unknown"))}</strong></div>
                <div class="vm-sidebar-kv">Role<strong>{current_role()}</strong></div>
                <div class="vm-sidebar-kv">Project<strong>Version Manager</strong></div>
                <div class="vm-sidebar-kv">Team<strong>{active_team}</strong></div>
                <div class="vm-sidebar-kv">Release Line<strong>{active_release}</strong></div>
                <div class="vm-sidebar-kv">Scope<strong>Version and Security Assessment</strong></div>
                <div class="vm-sidebar-kv">Workflow<strong>{workflow_status}</strong></div>
                <div class="vm-sidebar-kv">Last Scan<strong>{last_scan}</strong></div>
                <div class="vm-sidebar-kv">Next Scan<strong>{next_scan}</strong></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("Sign Out", use_container_width=True):
            clear_user_session()
            st.rerun()
        st.divider()
        return st.radio(
            "Navigation",
            pages,
            label_visibility="collapsed",
        )
