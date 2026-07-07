from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from App.auth import can_manage_settings


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
