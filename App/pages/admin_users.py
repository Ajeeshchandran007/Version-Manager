from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from App.auth import ROLE_ADMIN, ROLE_QA_ENGINEER, ROLE_RELEASE_ENGINEER, can_manage_settings, current_user
from App.user_store import DEFAULT_USER_DB, PERMISSION_QA_SIGNOFF, ROLES, delete_user, list_user_audit, list_users, set_user_active, upsert_user
from App.workspace import list_teams


ACTION_ROLES = {ROLE_ADMIN, ROLE_RELEASE_ENGINEER, ROLE_QA_ENGINEER}
ADMIN_ROLES = {ROLE_ADMIN}


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
