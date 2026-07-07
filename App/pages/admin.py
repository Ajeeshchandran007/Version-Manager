from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from App.auth import (
    ROLE_ADMIN,
    ROLE_QA_ENGINEER,
    ROLE_RELEASE_ENGINEER,
    can_manage_settings,
    current_role,
    current_user,
)
from App.formatting import format_duration_ms, format_ts
from App.user_store import (
    DEFAULT_USER_DB,
    PERMISSION_QA_SIGNOFF,
    ROLES,
    delete_user,
    list_user_audit,
    list_users,
    set_user_active,
    upsert_user,
)
from App.workspace import BASE_DIR, DEFAULT_TEAM_LABEL, active_release_line, active_team_name, list_teams


ACTION_ROLES = {ROLE_ADMIN, ROLE_RELEASE_ENGINEER, ROLE_QA_ENGINEER}
ADMIN_ROLES = {ROLE_ADMIN}
PRIMARY_CACHE_NAMESPACES = {"software_versions", "vulnerabilities", "nvd"}
CACHE_NAMESPACE_LABELS = {
    "software_versions": "Software Versions",
    "vulnerabilities": "Vulnerability Assessments",
    "nvd": "NVD CVE Lookup",
    "tavily": "Tavily Search",
    "openai_analysis": "OpenAI Analysis",
    "vendor_sources": "Vendor Sources",
}


def friendly_event(metric: str) -> tuple[str, str, str]:
    mapping = {
        "fetch_latest_versions.duration_ms": (
            "Latest versions refreshed",
            "The system checked the latest approved vendor versions.",
            "Version Catalog",
        ),
        "fetch_current_versions.duration_ms": (
            "Current inventory refreshed",
            "The system collected installed versions from servers or fallback documents.",
            "Inventory",
        ),
        "compare_versions.duration_ms": (
            "Version comparison completed",
            "Installed versions were compared against the latest-version catalog.",
            "Compliance",
        ),
        "check_vulnerabilities.duration_ms": (
            "Vulnerability assessment completed",
            "Current installed versions were checked against vulnerability data.",
            "Security",
        ),
        "send_notification.duration_ms": (
            "Email notification processed",
            "The version assessment report email was generated and sent using configured mail settings.",
            "Notification",
        ),
    }
    return mapping.get(metric, ("Workflow event recorded", "A system workflow event was recorded.", "System"))


def build_audit_events(metrics_df: pd.DataFrame, cache_metrics: dict[str, Any]) -> pd.DataFrame:
    rows = []
    if not metrics_df.empty:
        for _, item in metrics_df.tail(100).iterrows():
            labels = item.get("labels", {})
            if not isinstance(labels, dict):
                labels = {}
            title, description, category = friendly_event(str(item.get("metric", "")))
            rows.append(
                {
                    "Timestamp": format_ts(str(item.get("ts", ""))),
                    "Category": category,
                    "Activity": title,
                    "Status": str(labels.get("status", "ok")).title(),
                    "Duration": format_duration_ms(item.get("value")),
                    "Details": description,
                    "Trace ID": item.get("trace_id", ""),
                    "Technical Event": item.get("metric", ""),
                }
            )

    cache_updated = format_ts(cache_metrics.get("last_updated")) if cache_metrics else "Not available"
    for namespace, record in cache_metrics.items():
        if isinstance(record, dict):
            hits = int(record.get("hits", 0))
            misses = int(record.get("misses", 0))
            saved = int(record.get("estimated_api_calls_saved", 0))
            rows.append(
                {
                    "Timestamp": cache_updated,
                    "Category": "Cache",
                    "Activity": f"{namespace.replace('_', ' ').title()} cache updated",
                    "Status": "Ok",
                    "Duration": "Not applicable",
                    "Details": f"{hits} cache hits, {misses} misses, {saved} API calls saved.",
                    "Trace ID": "",
                    "Technical Event": namespace,
                }
            )
    return pd.DataFrame(rows)


def render_audit(metrics_df: pd.DataFrame, cache_metrics: dict[str, Any], ctx: Any) -> None:
    ctx.section_title("Activity History", "Readable operational history for scans, reports, cache usage, and notifications.")
    df = build_audit_events(metrics_df, cache_metrics)
    if df.empty:
        st.info("No activity history is available yet. Run the pipeline to create workflow events.")
        return

    total_events = len(df)
    successful = int((df["Status"].str.upper() == "OK").sum())
    cache_events = int((df["Category"] == "Cache").sum())
    last_event = df["Timestamp"].iloc[-1] if not df.empty else "Not available"
    cols = st.columns(4)
    cols[0].metric("Activities Recorded", total_events)
    cols[1].metric("Successful Events", successful)
    cols[2].metric("Cache Events", cache_events)
    cols[3].metric("Latest Activity", last_event)

    display_cols = ["Timestamp", "Category", "Activity", "Status", "Duration", "Details"]
    ctx.searchable_table(df[display_cols], "activity_history", ["Category", "Status"])

    with st.expander("Technical audit details"):
        technical_cols = ["Timestamp", "Technical Event", "Trace ID", "Status", "Duration"]
        st.dataframe(df[technical_cols], use_container_width=True, hide_index=True)



def render_settings(config: dict[str, Any], ctx: Any) -> None:
    if not can_manage_settings():
        ctx.render_access_denied("Admin")
        return

    ctx.section_title("Settings", "Runtime controls and integration status.")
    cache_config = config.get("cache", {})
    vuln_config = config.get("vulnerability", {})
    smtp_config = config.get("smtp", {})

    col1, col2 = st.columns(2)
    with col1:
        force_refresh = st.toggle(
            "Get Fresh Data",
            value=False,
            help="Turn on to bypass cached results and retrieve fresh vendor and vulnerability data. Leave off to use cache when valid.",
        )
        email_enabled = st.toggle("Enable Email Notifications", value=bool(smtp_config.get("server")), help="Requires SMTP configuration.")
        st.markdown(
            f"""
            <div class="vm-card">
                <strong>Runtime Mode</strong><br>
                <div class="vm-posture-note">Data refresh mode: {"Fresh data requested" if force_refresh else "Cache enabled"}</div>
                <div class="vm-posture-note">Email notifications: {"Enabled" if email_enabled else "Disabled"}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col2:
        software_ttl_days = int(cache_config.get("software_versions_ttl_seconds", 604800) / 86400)
        vuln_ttl_hours = int(cache_config.get("vulnerabilities_ttl_seconds", 86400) / 3600)
        st.number_input("Software Versions TTL - Days", min_value=1, max_value=30, value=software_ttl_days)
        st.number_input("Vulnerability TTL - Hours", min_value=1, max_value=168, value=vuln_ttl_hours)
        st.caption("Configuration values shown here reflect the active project settings.")

    st.subheader("Integration Status")
    status_rows = [
        {"Integration": "NVD API", "Status": "Configured" if vuln_config.get("nvd_api_key") else "Missing", "Details": "Vulnerability CVE source"},
        {"Integration": "SMTP", "Status": "Configured" if smtp_config.get("server") else "Missing", "Details": "Email notification delivery"},
        {"Integration": "Cache", "Status": "Enabled" if cache_config.get("enabled", True) else "Disabled", "Details": cache_config.get("backend", "json")},
    ]
    st.dataframe(ctx.style_operational_table(pd.DataFrame(status_rows)), use_container_width=True, hide_index=True)


def render_admin_user_management(ctx: Any) -> None:
    if not can_manage_settings():
        ctx.render_access_denied("Admin")
        return

    ctx.section_title("Admin User Management", "Create users, assign roles, control team scope, and audit account changes.")
    action_status = st.session_state.get("admin_user_action_status")
    st.subheader("Access Control")
    st.caption("Release Engineer prepares assessments and reports. QA Engineer validates deployments. Admin manages users, teams, and settings.")
    current_username = current_user().get("username", "admin")
    user_rows = [
        {
            "Delete?": False,
            "User": user["username"],
            "Display Name": user.get("display_name", user["username"]),
            "Role": user["role"],
            "Team Scope": ", ".join(user.get("team_scope", ["*"])),
            "QA Signoff": "Yes" if PERMISSION_QA_SIGNOFF in user.get("permissions", []) else "No",
            "Account Status": "Active" if user.get("active", True) else "Inactive - login disabled",
            "Last Login": user.get("last_login_at") or "Never",
            "Can Run Scans": "Yes" if user["role"] in ACTION_ROLES else "No",
            "Can Manage Settings": "Yes" if user["role"] in ADMIN_ROLES else "No",
        }
        for user in list_users(DEFAULT_USER_DB, include_inactive=True)
    ]
    user_table = pd.DataFrame(user_rows)
    edited_user_table = st.data_editor(
        user_table,
        use_container_width=True,
        hide_index=True,
        disabled=[column for column in user_table.columns if column != "Delete?"],
        column_config={
            "Delete?": st.column_config.CheckboxColumn(
                "Delete?",
                help="Select user account(s) created by mistake, then click Delete Checked User(s).",
                default=False,
            )
        },
        key="admin_user_delete_table",
    )
    delete_candidates = edited_user_table[edited_user_table["Delete?"]]["User"].tolist() if not edited_user_table.empty else []
    delete_disabled = not delete_candidates
    if st.button("Delete Checked User(s)", disabled=delete_disabled, use_container_width=True):
        if current_username in delete_candidates:
            st.session_state["admin_user_action_status"] = "Current logged-in user cannot be deleted."
            st.rerun()
        st.session_state["admin_user_pending_delete"] = delete_candidates
        st.rerun()
    pending_delete = st.session_state.get("admin_user_pending_delete", [])
    if pending_delete:
        st.warning(
            "You are about to permanently delete user account(s): "
            f"{', '.join(pending_delete)}. Audit history will be retained."
        )
        confirm_cols = st.columns(2)
        if confirm_cols[0].button("Confirm Delete", use_container_width=True):
            deleted_users = []
            for target_username in pending_delete:
                if delete_user(target_username, current_username, DEFAULT_USER_DB):
                    deleted_users.append(target_username)
            if deleted_users:
                st.session_state["admin_user_action_status"] = f"Deleted user(s): {', '.join(deleted_users)}."
                st.session_state["admin_user_reset_after_save"] = True
            else:
                st.session_state["admin_user_action_status"] = "No selected users were deleted."
            st.session_state.pop("admin_user_pending_delete", None)
            st.rerun()
        if confirm_cols[1].button("Cancel Delete", use_container_width=True):
            st.session_state["admin_user_action_status"] = "Delete cancelled."
            st.session_state.pop("admin_user_pending_delete", None)
            st.rerun()
    st.caption("Inactive users are retained here for audit history, but they cannot sign in.")

    st.subheader("Create or Update User")
    st.caption("Create users, assign roles, limit team scope, and deactivate access without editing config.json.")
    if st.session_state.pop("admin_user_reset_after_save", False):
        st.session_state["admin_user_action"] = "Create new user"
        st.session_state["admin_user_form_reset_token"] = st.session_state.get("admin_user_form_reset_token", 0) + 1
    form_reset_token = st.session_state.setdefault("admin_user_form_reset_token", 0)
    existing_users = list_users(DEFAULT_USER_DB, include_inactive=True)
    usernames = ["Create new user"] + [user["username"] for user in existing_users]
    selected_username = st.selectbox("User Action", usernames, key="admin_user_action")
    selected_user = next((user for user in existing_users if user["username"] == selected_username), None)
    form_key_suffix = selected_user["username"] if selected_user else f"new_{form_reset_token}"

    with st.form("admin_user_management_form"):
        identity_cols = st.columns(2)
        with identity_cols[0]:
            username = st.text_input(
                "Username",
                value="" if selected_user is None else selected_user["username"],
                disabled=selected_user is not None,
                key=f"admin_user_username_{form_key_suffix}",
            )
        with identity_cols[1]:
            display_name = st.text_input(
                "Display Name",
                value="" if selected_user is None else selected_user.get("display_name", ""),
                key=f"admin_user_display_name_{form_key_suffix}",
            )
        access_cols = st.columns([3, 1])
        with access_cols[0]:
            role = st.selectbox(
                "Role",
                sorted(ROLES),
                index=sorted(ROLES).index(selected_user["role"]) if selected_user else 0,
                key=f"admin_user_role_{form_key_suffix}",
            )
        with access_cols[1]:
            st.markdown("<div style='height: 1.65rem'></div>", unsafe_allow_html=True)
            active = st.checkbox(
                "Active",
                value=True if selected_user is None else bool(selected_user.get("active", True)),
                key=f"admin_user_active_{form_key_suffix}",
            )
        permission_cols = st.columns([3, 1])
        with permission_cols[0]:
            qa_signoff_permission = st.checkbox(
                "Allow QA Completion Signoff",
                value=PERMISSION_QA_SIGNOFF in (selected_user or {}).get("permissions", []),
                help="Allows this user to perform final QA signoff for assigned team/release scope.",
                key=f"admin_user_permission_qa_signoff_{form_key_suffix}",
            )
        with permission_cols[1]:
            st.caption("Admin receives signoff permission automatically on save.")
        available_teams = list_teams()
        existing_scope = selected_user.get("team_scope", ["*"]) if selected_user else []
        all_teams_label = "All Teams"
        team_scope_options = [all_teams_label, *available_teams]
        default_team_scope = (
            [all_teams_label]
            if "*" in existing_scope or (selected_user is None and role == ROLE_ADMIN)
            else [team for team in existing_scope if team in available_teams]
        )
        selected_team_scope = st.multiselect(
            "Team Scope",
            team_scope_options,
            default=default_team_scope,
            placeholder="Search and select teams",
            help="Choose All Teams for wildcard access, or choose one or more specific teams.",
            key=f"admin_user_team_scope_{form_key_suffix}",
        )
        all_teams = all_teams_label in selected_team_scope
        selected_teams = [team for team in selected_team_scope if team != all_teams_label]
        if all_teams:
            st.caption("All current and future teams are included for this user.")
        else:
            st.caption(f"{len(selected_teams)} team(s) selected.")
        password = st.text_input(
            "Password",
            type="password",
            help="Required for new users. Leave blank when editing to keep the existing password.",
            key=f"admin_user_password_{form_key_suffix}",
        )
        submitted = st.form_submit_button("Save User", type="primary", use_container_width=True)

    if action_status:
        st.success(f"Last admin action: {action_status}")

    if submitted:
        try:
            if all_teams and selected_teams:
                raise ValueError("Choose either All Teams or specific teams, not both.")
            if not all_teams and not selected_teams:
                raise ValueError("Select at least one team, or choose All Teams.")
            saved = upsert_user(
                username=username if selected_user is None else selected_user["username"],
                password=password or None,
                display_name=display_name.strip() or username,
                role=role,
                team_scope=["*"] if all_teams else selected_teams,
                permissions=[PERMISSION_QA_SIGNOFF] if qa_signoff_permission or role == ROLE_ADMIN else [],
                active=active,
                actor=current_user().get("username", "admin"),
                db_path=DEFAULT_USER_DB,
            )
            action_name = "created" if selected_user is None else "updated"
            st.session_state["admin_user_action_status"] = f"User {saved['username']} {action_name} successfully."
            st.session_state["admin_user_reset_after_save"] = True
            st.rerun()
        except Exception as exc:
            error_message = f"User was not saved: {exc}"
            st.session_state["admin_user_action_status"] = error_message
            st.error(error_message)

    if selected_user and selected_user["username"] != current_user().get("username"):
        delete_confirmed = st.checkbox(
            f"Confirm delete user {selected_user['username']}",
            help="Use delete only for accounts created by mistake. Deactivate is preferred when history should remain visible.",
            key=f"admin_user_delete_confirm_{selected_user['username']}",
        )
        cols = st.columns(3)
        if cols[0].button("Deactivate Selected User", disabled=not selected_user.get("active", True), use_container_width=True):
            set_user_active(selected_user["username"], False, current_user().get("username", "admin"), DEFAULT_USER_DB)
            st.session_state["admin_user_action_status"] = (
                f"User {selected_user['username']} deactivated successfully. Login is now disabled."
            )
            st.session_state["admin_user_reset_after_save"] = True
            st.rerun()
        if cols[1].button("Reactivate Selected User", disabled=selected_user.get("active", True), use_container_width=True):
            set_user_active(selected_user["username"], True, current_user().get("username", "admin"), DEFAULT_USER_DB)
            st.session_state["admin_user_action_status"] = (
                f"User {selected_user['username']} reactivated successfully. Login is now enabled."
            )
            st.session_state["admin_user_reset_after_save"] = True
            st.rerun()
        if cols[2].button("Delete Selected User", disabled=not delete_confirmed, use_container_width=True):
            deleted = delete_user(selected_user["username"], current_user().get("username", "admin"), DEFAULT_USER_DB)
            st.session_state["admin_user_action_status"] = (
                f"User {selected_user['username']} deleted successfully."
                if deleted
                else f"User {selected_user['username']} was not found."
            )
            st.session_state["admin_user_reset_after_save"] = True
            st.rerun()

    with st.expander("User Audit Events", expanded=False):
        audit_rows = list_user_audit(DEFAULT_USER_DB, limit=100)
        if audit_rows:
            st.dataframe(ctx.style_operational_table(pd.DataFrame(audit_rows)), use_container_width=True, hide_index=True)
        else:
            st.info("No user audit events recorded yet.")



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


def render_cache(cache_metrics: dict[str, Any], ctx: Any) -> None:
    ctx.section_title("Cache Analytics", "Operational cache utilization, API reduction, and token savings.")
    namespaces = {key: val for key, val in cache_metrics.items() if isinstance(val, dict)}
    rows = []
    for namespace, record in namespaces.items():
        hits = int(record.get("hits", 0))
        misses = int(record.get("misses", 0))
        total = hits + misses
        rows.append(
            {
                "Namespace": namespace,
                "Cache Area": CACHE_NAMESPACE_LABELS.get(namespace, namespace.replace("_", " ").title()),
                "Cache Hits": hits,
                "Cache Misses": misses,
                "Hit Ratio": round((hits / total) * 100, 1) if total else 0,
                "API Calls Saved": int(record.get("estimated_api_calls_saved", 0)),
                "Estimated Token Savings": int(record.get("estimated_tokens_saved", 0)),
                "Bypasses": int(record.get("bypasses", 0)),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        st.info("No cache metrics available.")
        return
    primary_df = df[df["Namespace"].isin(PRIMARY_CACHE_NAMESPACES)].copy()
    advanced_df = df[~df["Namespace"].isin(PRIMARY_CACHE_NAMESPACES)].copy()
    if primary_df.empty:
        primary_df = df.copy()

    totals = primary_df[["Cache Hits", "Cache Misses", "API Calls Saved", "Estimated Token Savings"]].sum()
    cols = st.columns(5)
    cols[0].metric("Cache Hits", int(totals["Cache Hits"]))
    cols[1].metric("Cache Misses", int(totals["Cache Misses"]))
    total_requests = totals["Cache Hits"] + totals["Cache Misses"]
    cols[2].metric("Hit Ratio", f"{round((totals['Cache Hits'] / total_requests) * 100, 1) if total_requests else 0}%")
    cols[3].metric("API Calls Saved", int(totals["API Calls Saved"]))
    cols[4].metric("Token Savings", int(totals["Estimated Token Savings"]))

    display_cols = ["Cache Area", "Cache Hits", "Cache Misses", "Hit Ratio", "API Calls Saved", "Estimated Token Savings", "Bypasses"]
    st.dataframe(primary_df[display_cols], use_container_width=True, hide_index=True)
    left, right = st.columns(2)
    with left:
        ctx.bar_chart(primary_df, "Cache Area", "Hit Ratio", "Cache Efficiency", "Cache Area")
    with right:
        ctx.bar_chart(primary_df, "Cache Area", "API Calls Saved", "API Calls Avoided", "Cache Area")

    if not advanced_df.empty:
        with st.expander("Advanced cache details"):
            st.caption("Internal cache layers used for troubleshooting vendor search, LLM parsing, and direct source fetches.")
            st.dataframe(advanced_df[display_cols], use_container_width=True, hide_index=True)


