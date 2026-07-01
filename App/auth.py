from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Callable

import pandas as pd
import streamlit as st

ROLE_ADMIN = "Admin"
ROLE_RELEASE_ENGINEER = "Release Engineer"
ROLE_QA_ENGINEER = "QA Engineer"
ACTION_ROLES = {ROLE_ADMIN, ROLE_RELEASE_ENGINEER, ROLE_QA_ENGINEER}
ADMIN_ROLES = {ROLE_ADMIN}


def normalize_team_scope(raw_scope: Any) -> list[str]:
    if raw_scope in (None, "", []):
        return ["*"]
    if isinstance(raw_scope, str):
        scope = [part.strip() for part in raw_scope.split(",")]
    elif isinstance(raw_scope, list):
        scope = [str(part).strip() for part in raw_scope]
    else:
        scope = [str(raw_scope).strip()]
    cleaned = [team for team in scope if team]
    return cleaned or ["*"]


def normalize_role(role: Any) -> str:
    value = str(role or ROLE_QA_ENGINEER).strip().lower()
    if value == "admin":
        return ROLE_ADMIN
    if value in {"operator", "packager", "release engineer", "release_engineer", "release-engineer"}:
        return ROLE_RELEASE_ENGINEER
    if value in {"viewer", "tester", "qa", "qa engineer", "qa_engineer", "qa-engineer"}:
        return ROLE_QA_ENGINEER
    return ROLE_QA_ENGINEER


def configured_users(config: dict[str, Any]) -> list[dict[str, Any]]:
    users = config.get("users")
    if isinstance(users, list) and users:
        return [
            {
                "username": str(user.get("username", "")).strip(),
                "password": str(user.get("password", "")),
                "role": normalize_role(user.get("role")),
                "display_name": str(user.get("display_name") or user.get("username") or "").strip(),
                "team_scope": normalize_team_scope(user.get("team_scope")),
            }
            for user in users
            if isinstance(user, dict) and user.get("username")
        ]
    return [
        {"username": "admin", "password": "admin", "role": ROLE_ADMIN, "display_name": "Administrator", "team_scope": ["*"]},
        {"username": "sourceone_release", "password": "sourceone_release", "role": ROLE_RELEASE_ENGINEER, "display_name": "SourceOne Release", "team_scope": ["SourceOne"]},
        {"username": "sourceone_qa", "password": "sourceone_qa", "role": ROLE_QA_ENGINEER, "display_name": "SourceOne QA", "team_scope": ["SourceOne"]},
        {"username": "dps_release", "password": "dps_release", "role": ROLE_RELEASE_ENGINEER, "display_name": "DPS Release", "team_scope": ["DPS"]},
        {"username": "dps_qa", "password": "dps_qa", "role": ROLE_QA_ENGINEER, "display_name": "DPS QA", "team_scope": ["DPS"]},
        {"username": "avamar_release", "password": "avamar_release", "role": ROLE_RELEASE_ENGINEER, "display_name": "Avamar Release", "team_scope": ["Avamar"]},
        {"username": "avamar_qa", "password": "avamar_qa", "role": ROLE_QA_ENGINEER, "display_name": "Avamar QA", "team_scope": ["Avamar"]},
        {"username": "package_release", "password": "package_release", "role": ROLE_RELEASE_ENGINEER, "display_name": "Package Team Release", "team_scope": ["PackageTeam"]},
        {"username": "package_qa", "password": "package_qa", "role": ROLE_QA_ENGINEER, "display_name": "Package Team QA", "team_scope": ["PackageTeam"]},
    ]


def current_user() -> dict[str, Any]:
    return st.session_state.get("vm_user") or {}


def current_role() -> str:
    return normalize_role(current_user().get("role"))


def user_team_scope(user: dict[str, Any] | None = None) -> list[str]:
    user = user or current_user()
    return normalize_team_scope(user.get("team_scope"))


def can_run_operations() -> bool:
    return current_role() in ACTION_ROLES


def can_manage_settings() -> bool:
    return current_role() in ADMIN_ROLES


def _auth_secret(config: dict[str, Any]) -> bytes:
    auth_cfg = config.get("auth", {})
    configured_secret = str(auth_cfg.get("session_secret") or "").strip()
    if configured_secret:
        return configured_secret.encode("utf-8")
    seed = json.dumps(
        {
            "users": [
                {
                    "username": user["username"],
                    "password": user["password"],
                    "role": user["role"],
                    "team_scope": user.get("team_scope", ["*"]),
                }
                for user in configured_users(config)
            ],
            "smtp_sender": (config.get("smtp") or {}).get("sender", ""),
        },
        sort_keys=True,
    )
    return hashlib.sha256(seed.encode("utf-8")).digest()


def _auth_token_ttl_seconds(config: dict[str, Any]) -> int:
    auth_cfg = config.get("auth", {})
    try:
        hours = float(auth_cfg.get("session_ttl_hours", 12))
    except (TypeError, ValueError):
        hours = 12
    return max(1, int(hours * 3600))


def _sign_auth_payload(payload: dict[str, Any], config: dict[str, Any]) -> str:
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_json).decode("ascii").rstrip("=")
    signature = hmac.new(_auth_secret(config), payload_b64.encode("ascii"), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{payload_b64}.{sig_b64}"


def _decode_auth_token(token: str, config: dict[str, Any]) -> dict[str, Any] | None:
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        expected = hmac.new(_auth_secret(config), payload_b64.encode("ascii"), hashlib.sha256).digest()
        actual = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
        if not hmac.compare_digest(expected, actual):
            return None
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
    except (ValueError, json.JSONDecodeError, TypeError):
        return None

    if int(payload.get("expires_at", 0)) < int(time.time()):
        return None

    token_user = str(payload.get("username") or "").strip()
    for user in configured_users(config):
        if user["username"].lower() == token_user.lower():
            return {
                "username": user["username"],
                "role": user["role"],
                "display_name": user["display_name"] or user["username"],
                "team_scope": user.get("team_scope", ["*"]),
            }
    return None


def _query_param_value(name: str) -> str | None:
    value = st.query_params.get(name)
    if isinstance(value, list):
        return str(value[0]) if value else None
    return str(value) if value else None


def _restore_user_from_auth_token(
    config: dict[str, Any],
    allowed_teams_resolver: Callable[[dict[str, Any] | None], list[str]],
) -> bool:
    token = _query_param_value("vm_session")
    if not token:
        return False
    user = _decode_auth_token(token, config)
    if not user:
        st.query_params.pop("vm_session", None)
        return False
    st.session_state["vm_user"] = user
    scoped_teams = allowed_teams_resolver(user)
    if len(scoped_teams) == 1:
        st.session_state["active_team"] = scoped_teams[0]
    return True


def _persist_user_session(user: dict[str, Any], config: dict[str, Any]) -> None:
    payload = {
        "username": user["username"],
        "issued_at": int(time.time()),
        "expires_at": int(time.time()) + _auth_token_ttl_seconds(config),
    }
    st.query_params["vm_session"] = _sign_auth_payload(payload, config)


def clear_user_session() -> None:
    st.session_state.pop("vm_user", None)
    st.query_params.pop("vm_session", None)


def require_login(
    config: dict[str, Any],
    allowed_teams_resolver: Callable[[dict[str, Any] | None], list[str]],
) -> bool:
    auth_cfg = config.get("auth", {})
    if auth_cfg.get("enabled", True) is False:
        st.session_state.setdefault(
            "vm_user",
            {"username": "local", "role": ROLE_ADMIN, "display_name": "Local Admin", "team_scope": ["*"]},
        )
        return True
    if current_user():
        return True
    if _restore_user_from_auth_token(config, allowed_teams_resolver):
        return True

    login_placeholder = st.empty()
    with login_placeholder.container():
        _, center, _ = st.columns([1.2, 1, 1.2])
        with center:
            st.markdown(
                """
                <div class="vm-login-wrap">
                    <div class="vm-login-brand">Version Manager</div>
                    <div class="vm-login-subtitle">
                        Sign in to access software version monitoring, vulnerability assessment, reports, and operational controls.
                    </div>
                    <div class="vm-login-meta">
                        <span class="vm-chip">Enterprise Access</span>
                        <span class="vm-chip">Role Based</span>
                        <span class="vm-chip">Production</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            with st.form("vm_login_form"):
                username = st.text_input("Username")
                password = st.text_input("Password", type="password")
                submitted = st.form_submit_button("Sign In", type="primary", use_container_width=True)
            with st.expander("Available roles", expanded=False):
                role_rows = [
                    {
                        "Username": user["username"],
                        "Role": user["role"],
                        "Team Scope": "All Teams" if "*" in user.get("team_scope", ["*"]) else ", ".join(user.get("team_scope", [])),
                    }
                    for user in configured_users(config)
                ]
                st.dataframe(pd.DataFrame(role_rows), use_container_width=True, hide_index=True)
            if submitted:
                for user in configured_users(config):
                    if username.strip().lower() == user["username"].lower() and password.strip() == user["password"]:
                        authenticated_user = {
                            "username": user["username"],
                            "role": user["role"],
                            "display_name": user["display_name"] or user["username"],
                            "team_scope": user.get("team_scope", ["*"]),
                        }
                        st.session_state["vm_user"] = authenticated_user
                        scoped_teams = allowed_teams_resolver(authenticated_user)
                        if len(scoped_teams) == 1:
                            st.session_state["active_team"] = scoped_teams[0]
                        _persist_user_session(authenticated_user, config)
                        login_placeholder.empty()
                        return True
                st.error("Invalid username or password.")

    return False
