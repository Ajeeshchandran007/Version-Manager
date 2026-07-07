from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from App.auth import (
    ROLE_ADMIN,
    ROLE_QA_ENGINEER,
    ROLE_RELEASE_ENGINEER,
    current_role,
    user_team_scope,
)
from App.workspace import (
    active_release_line,
    active_team_name,
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

from App.pages.context import render_context_selector


def render_dashboard(current_df: pd.DataFrame, comparison_df: pd.DataFrame, vuln_df: pd.DataFrame, metrics_df: pd.DataFrame, ctx: Any) -> None:
    role = current_role()
    team = active_team_name()
    title_prefix = "Enterprise" if "*" in user_team_scope() and role == ROLE_ADMIN else team
    subtitle_context = f"{team} context"
    if role == ROLE_RELEASE_ENGINEER:
        ctx.section_title(f"{title_prefix} Release Dashboard", f"Package readiness, version drift, upgrade planning, and security visibility for {subtitle_context}.")
    elif role == ROLE_QA_ENGINEER:
        ctx.section_title(f"{title_prefix} QA Dashboard", f"Installation validation, compatibility review, version status, and report access for {subtitle_context}.")
    else:
        ctx.section_title(f"{title_prefix} Administrator Dashboard", f"Operational posture, update exposure, security risk, and platform controls for {subtitle_context}.")
    total = len(current_df)
    updates = int((comparison_df["Need Update"] == "Yes").sum()) if not comparison_df.empty else 0
    up_to_date = max(total - updates, 0)
    risk_counts = vuln_df["Risk Level"].value_counts().to_dict() if not vuln_df.empty else {}
    ctx.render_posture_strip(comparison_df, vuln_df)

    cols = st.columns(4)
    metrics = [
        ("Total Applications", total, None),
        ("Requiring Update", updates, None),
        ("Up-to-Date", up_to_date, None),
        ("Security Risk Items", risk_counts.get("CRITICAL", 0) + risk_counts.get("HIGH", 0) + risk_counts.get("MEDIUM", 0), None),
    ]
    for col, (label, val, delta) in zip(cols, metrics):
        col.metric(label, val, delta)

    left, right = st.columns(2)
    with left:
        if not comparison_df.empty:
            gap_df = comparison_df["Version Gap"].value_counts().reset_index()
            gap_df.columns = ["Version Gap", "Count"]
            ctx.bar_chart(gap_df, "Version Gap", "Count", "Version Gap Distribution")
    with right:
        if not comparison_df.empty:
            priority_df = comparison_df["Update Priority"].value_counts().reset_index()
            priority_df.columns = ["Update Priority", "Count"]
            ctx.bar_chart(priority_df, "Update Priority", "Count", "Remediation Priority")

    st.subheader("Top Applications Requiring Immediate Update")
    if not comparison_df.empty:
        priority_rank = {"Critical": 1, "High": 2, "Medium": 3, "Low": 4, "None": 5}
        top = comparison_df[comparison_df["Need Update"] == "Yes"].copy()
        top["Priority Rank"] = top["Update Priority"].map(priority_rank).fillna(9)
        st.dataframe(
            ctx.style_operational_table(
                top.sort_values(["Priority Rank", "Software Name"]).head(10)[
                    ["Software Name", "Current Version", "Latest Version", "Version Gap", "Update Priority", "Risk Level"]
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )

def render_dashboard_page(current_df: pd.DataFrame, comparison_df: pd.DataFrame, vuln_df: pd.DataFrame, metrics_df: pd.DataFrame, ctx: Any) -> None:
    render_context_selector(ctx, "dashboard")
    st.caption(f"Viewing: {active_team_name()} / {active_release_line()}")
    render_dashboard(current_df, comparison_df, vuln_df, metrics_df, ctx)

def render_inventory(current_df: pd.DataFrame, ctx: Any) -> None:
    ctx.section_title("Software Inventory", "Installed software inventory from live servers and document extraction.")
    if current_df.empty:
        configured_df, input_path = ctx.configured_inventory_from_active_software_yml()
        if configured_df.empty:
            st.warning(
                "No generated inventory records were found, and no active software.yml input was found for "
                f"{active_team_name()} / {active_release_line()}."
            )
            st.caption(f"Expected input file: {input_path}")
            return
        st.info(
            "No scan output is available yet. Showing configured software from the active software.yml input. "
            "Run the pipeline to populate discovered current versions, server details, QA data, and reports."
        )
        st.caption(f"Loaded input file: {input_path}")
        ctx.searchable_table(configured_df.drop(columns=["Source"], errors="ignore"), "software_inventory_configured", ["Vendor", "Environment"])
        return
    display_df = current_df.drop(columns=["Source"], errors="ignore")
    ctx.searchable_table(display_df, "software_inventory", ["Vendor", "Environment"])

def render_latest(latest_df: pd.DataFrame, ctx: Any) -> None:
    ctx.section_title("Latest Versions", "Approved latest-version catalog with source and cache provenance.")
    if latest_df.empty:
        st.info("No latest version records found.")
        return
    display = latest_df.drop(columns=["Cache Status"], errors="ignore")
    ctx.searchable_table(display, "latest_versions", ["Vendor", "Source"])

def render_comparison(comparison_df: pd.DataFrame, ctx: Any) -> None:
    ctx.section_title("Version Comparison", "Current versus latest version analysis and update prioritization.")
    if comparison_df.empty:
        st.info("No comparison data found.")
        return
    score = ctx.compliance_score(comparison_df)
    col1, col2, col3 = st.columns(3)
    col1.metric("Compliance Percentage", f"{score}%")
    col2.metric("Outdated Applications", int((comparison_df["Need Update"] == "Yes").sum()) if not comparison_df.empty else 0)
    col3.metric("Critical or Major Gaps", int(comparison_df["Version Gap"].isin(["Major Gap", "CU Gap"]).sum()) if not comparison_df.empty else 0)

    ctx.searchable_table(comparison_df, "version_comparison", ["Need Update", "Version Gap", "Update Priority"])

    st.subheader("Version Drift Analysis")
    if not comparison_df.empty:
        drift = comparison_df["Version Gap"].value_counts().reset_index()
        drift.columns = ["Version Gap", "Count"]
        ctx.bar_chart(drift, "Version Gap", "Count", "Version Drift by Gap Type")

def render_package_readiness(readiness_df: pd.DataFrame, ctx: Any) -> None:
    if current_role() not in {ROLE_ADMIN, ROLE_RELEASE_ENGINEER}:
        ctx.render_access_denied("Administrator or Release Engineer")
        return
    ctx.section_title("Package Readiness", "Package preparation, vendor review, dependency validation, and upgrade impact.")
    if readiness_df.empty:
        st.info("No package readiness data found. Run version comparison first.")
        return
    counts = readiness_df["Package Readiness"].value_counts().to_dict()
    cols = st.columns(4)
    cols[0].metric("Ready", counts.get("Ready for Packaging", 0))
    cols[1].metric("Vendor Patch Available", counts.get("Vendor Patch Available", 0))
    cols[2].metric("Dependency Review", counts.get("Dependency Review Required", 0))
    cols[3].metric("Blocked", counts.get("Blocked", 0))
    ctx.searchable_table(
        readiness_df,
        "package_readiness",
        ["Package Readiness", "Upgrade Impact", "Owner", "Installer Type", "Vendor"],
    )

def render_compatibility_check(qa_df: pd.DataFrame, ctx: Any) -> None:
    ctx.section_title("Compatibility Check", "Operating system, runtime, browser, database, and architecture readiness for deployment validation.")
    if qa_df.empty:
        st.info("No compatibility data found. Run version comparison first.")
        return
    qa_df = ctx.add_environment_readiness(qa_df)
    review_required = int((qa_df["Compatibility Status"] != "Compatible").sum())
    cols = st.columns(3)
    cols[0].metric("Applications", len(qa_df))
    cols[1].metric("Review Required", review_required)
    cols[2].metric("Compatible", len(qa_df) - review_required)
    columns = [
        "Software Name",
        "Environment Readiness",
        "Current Version",
        "Latest Version",
        "Configured Environment",
        "Supported OS",
        "Supported Runtime",
        "Supported Browser",
        "Database Dependency",
        "Supported Architecture",
        "Requirement Source",
        "Requirement Confidence",
        "Last Verified",
    ]
    ctx.searchable_table(qa_df[columns], "compatibility_check", ["Environment Readiness", "Supported Architecture", "Requirement Confidence"])
