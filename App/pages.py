from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from App.auth import ROLE_ADMIN, ROLE_QA_ENGINEER, ROLE_RELEASE_ENGINEER, can_run_operations, current_role, user_team_scope
from App.workspace import (
    DEFAULT_TEAM_LABEL,
    WORKING_RELEASE_LABEL,
    active_output_path,
    active_release_line,
    active_team_name,
    allowed_teams_for_user,
    list_release_lines,
    project_path,
)


def render_context_selector(ctx: Any, location: str = "dashboard") -> None:
    teams = allowed_teams_for_user()
    current_team = active_team_name()
    if len(teams) == 1:
        st.text_input("Team / Product Stream", value=teams[0], disabled=True, key=f"{location}_team_locked")
        selected_team = teams[0]
    else:
        selected_team = st.selectbox(
            "Team / Product Stream",
            teams,
            index=teams.index(current_team) if current_team in teams else 0,
            key=f"{location}_team_selector",
        )

    if selected_team != st.session_state.get("active_team", DEFAULT_TEAM_LABEL):
        st.session_state["active_team"] = selected_team
        st.session_state.pop("active_release_line", None)
        ctx.clear_dashboard_cache()
        st.rerun()

    releases = list_release_lines(selected_team)
    current_release = active_release_line(selected_team)
    selected_release = st.selectbox(
        "Product Version / Release Line",
        releases,
        index=releases.index(current_release) if current_release in releases else 0,
        key=f"{location}_release_line_selector",
    )
    if selected_release != st.session_state.get("active_release_line", WORKING_RELEASE_LABEL):
        st.session_state["active_release_line"] = selected_release
        ctx.clear_dashboard_cache()
        st.rerun()


def render_operations(config: dict[str, Any], ctx: Any) -> None:
    if not can_run_operations():
        ctx.render_access_denied("Administrator, Release Engineer, or QA Engineer")
        return

    ctx.section_title("Operations", "Run scans now, review the automatic schedule, and execute individual validation steps.")
    st.markdown(
        """
        <div class="vm-card">
            <strong>How scans are triggered</strong>
            <div class="vm-posture-note">
                Scheduled scans run automatically when the backend service is running. Use the manual trigger when you need an immediate scan outside the normal schedule.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    category = "ALL"
    input_path = project_path(config.get("input_files", {}).get("software_yml", "Input/software.yml"))
    col1, col2, col3 = st.columns([1.1, 1, 1])
    with col1:
        st.metric("Run Context", active_team_name())
        st.caption(f"Release Line: {active_release_line()}")
        st.caption(f"Input: {input_path}")
    with col2:
        force_refresh = st.toggle(
            "Get Fresh Data",
            value=False,
            help="Turn on to bypass cached results and retrieve fresh vendor and vulnerability data. Leave off to use cache when valid.",
        )
    with col3:
        st.metric("Data Refresh Mode", "Fresh Data" if force_refresh else "Cache Enabled")

    st.subheader("Automatic Scan Schedule")
    current_schedule = config.get("schedule_cron", "0 9 * * 1")
    if "custom_schedule_expression" not in st.session_state:
        st.session_state["custom_schedule_expression"] = current_schedule
    schedule_presets = {
        "Weekly - Monday 09:00": "0 9 * * 1",
        "Daily - 09:00": "0 9 * * *",
        "Monthly - Day 1 09:00": "0 9 1 * *",
    }
    preset_names = list(schedule_presets.keys()) + ["Custom"]
    schedule_col0, schedule_col1, schedule_col2, schedule_col3 = st.columns([0.9, 1.2, 1.2, 1])
    with schedule_col0:
        st.metric("Automatic Scan", "Enabled" if current_schedule else "Not Configured")
    with schedule_col1:
        selected_schedule = st.selectbox("Schedule Preset", preset_names)
    with schedule_col2:
        if selected_schedule == "Custom":
            selected_cron = st.text_input(
                "Schedule Expression",
                value=st.session_state["custom_schedule_expression"],
                key="custom_schedule_input",
                help="Use standard cron format: minute hour day-of-month month day-of-week.",
            )
            st.session_state["custom_schedule_expression"] = selected_cron
        else:
            selected_cron = schedule_presets[selected_schedule]
            st.text_input("Schedule Expression", value=selected_cron, disabled=True)
    with schedule_col3:
        st.metric("Next Scan", ctx.describe_cron(selected_cron))

    valid_cron, cron_error = ctx.validate_cron_expression(selected_cron)
    if not valid_cron:
        st.error(f"Schedule expression is invalid: {cron_error}")
    save_col1, save_col2 = st.columns([0.7, 1.3])
    with save_col1:
        save_clicked = st.button("Save Automatic Schedule", disabled=not valid_cron, use_container_width=True)
    with save_col2:
        st.caption("Saving updates config.json and applies the schedule to this dashboard background scheduler while the dashboard is running.")
    if save_clicked:
        try:
            ctx.save_schedule_config(selected_cron)
            next_run = ctx.apply_background_schedule(selected_cron, category)
            st.session_state["schedule_save_result"] = {
                "status": "saved",
                "schedule": selected_cron,
                "description": ctx.describe_cron(selected_cron),
                "next_run": next_run,
            }
        except Exception as exc:
            st.session_state["schedule_save_result"] = {"status": "error", "error": str(exc)}

    schedule_result = st.session_state.get("schedule_save_result")
    if schedule_result:
        if schedule_result.get("status") == "saved":
            st.success(
                f"Automatic schedule saved: {schedule_result['description']}. "
                f"Next background run: {schedule_result['next_run']}."
            )
        else:
            st.error(f"Schedule was not saved: {schedule_result.get('error')}")

    qa_mode = current_role() == ROLE_QA_ENGINEER
    st.subheader("Manual Validation Trigger" if qa_mode else "Manual Scan Trigger")
    left, right = st.columns([1.2, 1])
    with left:
        button_label = "Run Validation Workflow" if qa_mode else "Run Scan Now"
        spinner_text = (
            "Running validation workflow: inventory, comparison, compatibility, and QA validation..."
            if qa_mode
            else "Running full pipeline: latest versions, current versions, comparison, vulnerability assessment, Excel, and email..."
        )
        if st.button(button_label, type="primary", use_container_width=True):
            with st.spinner(spinner_text):
                try:
                    result = ctx.run_async(ctx.trigger_full_pipeline(category, force_refresh))
                    ctx.clear_dashboard_cache()
                    st.session_state["last_operation_result"] = ctx.with_actor(result)
                except Exception as exc:
                    st.session_state["last_operation_result"] = ctx.with_actor({"error": str(exc)})
    with right:
        help_text = (
            "Runs the controlled backend workflow and refreshes compatibility and QA validation outputs for deployment testing."
            if qa_mode
            else "Runs latest-version lookup, current inventory collection, comparison, vulnerability assessment, Excel generation, and email reporting immediately."
        )
        st.markdown(
            f"""
            <div class="vm-card">
                <div class="vm-posture-note">
                    {help_text}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.subheader("Individual Actions")
    action_cols = st.columns(4)
    actions = [
        ("Fetch Latest Versions", "Fetching latest vendor versions...", lambda: ctx.run_async(ctx.trigger_fetch_latest_versions(category, force_refresh))),
        ("Fetch Current Versions", "Resolving current versions from servers/PDF...", lambda: ctx.run_async(ctx.trigger_fetch_current_versions(category))),
        ("Compare Versions", "Comparing current versions against latest versions...", ctx.trigger_compare_versions),
        ("Send Version Report Email", "Building and sending the version report email...", ctx.trigger_send_report_email),
    ]
    for col, (label, spinner, action) in zip(action_cols, actions):
        with col:
            if st.button(label, use_container_width=True):
                with st.spinner(spinner):
                    try:
                        result = action()
                        ctx.clear_dashboard_cache()
                        st.session_state["last_operation_result"] = ctx.with_actor(result)
                    except Exception as exc:
                        st.session_state["last_operation_result"] = ctx.with_actor({"error": str(exc)})

    st.subheader("Execution Summary")
    ctx.render_operation_result(st.session_state.get("last_operation_result"))


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

    st.subheader("Recent Scan Timeline")
    if not metrics_df.empty and {"metric", "value", "ts"}.issubset(metrics_df.columns):
        timeline = metrics_df.copy()
        timeline["Duration Seconds"] = timeline["value"].astype(float) / 1000
        st.dataframe(timeline[["ts", "metric", "Duration Seconds", "trace_id"]].tail(8), use_container_width=True, hide_index=True)
    else:
        st.info("No scan timeline metrics available.")


def render_dashboard_page(current_df: pd.DataFrame, comparison_df: pd.DataFrame, vuln_df: pd.DataFrame, metrics_df: pd.DataFrame, ctx: Any) -> None:
    render_context_selector(ctx, "dashboard")
    st.caption(f"Viewing: {active_team_name()} / {active_release_line()}")
    render_dashboard(current_df, comparison_df, vuln_df, metrics_df, ctx)


def render_reports(current_df: pd.DataFrame, comparison_df: pd.DataFrame, vuln_df: pd.DataFrame, ctx: Any) -> None:
    ctx.section_title("Reports", "Management and technical deliverables for review, download, and email distribution.")
    col1, col2, col3 = st.columns(3)
    col1.metric("Applications", len(current_df))
    col2.metric("Updates Required", int((comparison_df["Need Update"] == "Yes").sum()) if not comparison_df.empty else 0)
    col3.metric("Security Findings", int(vuln_df["CVE Count"].sum()) if not vuln_df.empty else 0)

    st.subheader("Report Package")
    email_html_file = active_output_path("email_preview.html")
    mime_by_name = {
        "email_preview.html": "text/html",
        "Software_Version_Assessment.xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Test_Case_Impact_Assessment.xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "package_readiness.json": "application/json",
        "qa_validation.json": "application/json",
        "testcase_impact.json": "application/json",
    }
    files = [
        (label, path, mime_by_name.get(path.name, "application/octet-stream"))
        for label, path in ctx.visible_output_files_for_role(current_role(), include_operational_reports=False)
    ]
    cols = st.columns(min(len(files), 4))
    for col, (label, path, mime) in zip(cols, files):
        with col:
            st.markdown(f"**{label}**")
            if "Technical" in label:
                st.caption("Detailed versions, CVE severity, risk, recommendations, and scan evidence.")
            elif "Package" in label:
                st.caption("Release engineering readiness, checklist, owner, installer type, and blockers.")
            elif "QA" in label:
                st.caption("Compatibility, installation validation, functional checks, and QA test notes.")
            elif "Test Case" in label:
                st.caption("Recommended QA regression and validation test cases for software requiring updates.")
            else:
                st.caption("Executive summary for managers, stakeholders, and email distribution.")
            st.caption(path.name)
            if path.exists():
                st.download_button(f"Download {label}", path.read_bytes(), file_name=path.name, mime=mime, use_container_width=True)
            else:
                st.warning("Not available")

    st.subheader("Management Report Preview")
    st.caption("This is the business-focused report body used for email notifications.")
    html = ctx.load_file_text(str(email_html_file), ctx.file_mtime(email_html_file))
    if html:
        components.html(html, height=760, scrolling=True)
    else:
        st.info("No HTML email preview found.")
