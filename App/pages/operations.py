from __future__ import annotations

from typing import Any

import streamlit as st

from App.auth import (
    ROLE_ADMIN,
    ROLE_QA_ENGINEER,
    ROLE_RELEASE_ENGINEER,
    can_run_operations,
    current_role,
)
from App.workspace import (
    active_release_line,
    active_team_name,
    project_path,
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


def render_operations(config: dict[str, Any], ctx: Any) -> None:
    if not can_run_operations():
        ctx.render_access_denied("Administrator, Release Engineer, or QA Engineer")
        return

    ctx.section_title("Operations", "Run scans now, review the automatic schedule, and execute individual validation steps.")
    release_context = render_context_selector(ctx, "operations")
    release_context_ready = bool(release_context.get("ready"))
    run_team = str(release_context.get("team") or active_team_name())
    run_release = str(release_context.get("release") or active_release_line(run_team))
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
        st.caption(f"Release Line: {active_release_line() or 'Missing release input'}")
        st.caption(f"Input: {input_path if release_context_ready else 'Not available'}")
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
        st.caption("Saving updates the runtime schedule configuration and applies it to this dashboard background scheduler while the dashboard is running.")
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

    st.markdown("<div style='height: 32px;'></div>", unsafe_allow_html=True)
    st.subheader("Manual Actions")
    st.caption("Run an on-demand scan, refresh your role workflow, or send a role-specific report email.")

    role = current_role()
    qa_mode = role == ROLE_QA_ENGINEER
    package_mode = role == ROLE_RELEASE_ENGINEER
    role_workflow_label = (
        "Run QA Workflow"
        if qa_mode
        else ("Run Package Workflow" if package_mode else "Run Full Pipeline")
    )
    role_email_label = (
        "Send QA Report Email"
        if qa_mode
        else ("Send Package Report Email" if package_mode else "Send Version Report Email")
    )
    if qa_mode:
        role_workflow_action = lambda: ctx.run_async(ctx.trigger_qa_workflow(category, force_refresh, run_team, run_release))
    elif package_mode:
        role_workflow_action = lambda: ctx.run_async(ctx.trigger_package_workflow(category, force_refresh, run_team, run_release))
    else:
        role_workflow_action = lambda: ctx.run_async(ctx.trigger_full_pipeline(category, force_refresh, run_team, run_release))

    workflow_cols = st.columns(3)
    workflow_actions = [
        (
            "Version Scan",
            "Run Scan",
            "Finds latest/current versions, compares them, and refreshes compatibility data without updating package or security-owned outputs.",
            "Running shared version scan...",
            lambda: ctx.run_async(ctx.trigger_shared_scan(category, force_refresh, run_team, run_release)),
            True,
        ),
        (
            "Role Workflow",
            role_workflow_label,
            (
                "Refreshes QA validation and testcase impact without updating package-owned files."
                if qa_mode
                else (
                    "Refreshes package readiness and supporting package outputs for the selected release."
                    if package_mode
                    else "Runs the complete admin workflow across shared scan, package readiness, QA validation, and reports."
                )
            ),
            f"Running {role_workflow_label.lower()}...",
            role_workflow_action,
            False,
        ),
        (
            "Communication",
            role_email_label,
            "Sends a summary email with role-specific supporting attachments for detailed review.",
            "Building and sending role report email...",
            ctx.trigger_send_report_email,
            False,
        ),
    ]
    for col, (section, label, description, spinner, action, primary) in zip(workflow_cols, workflow_actions):
        with col:
            st.markdown(f"**{section}**")
            if st.button(label, type="primary" if primary else "secondary", use_container_width=True, disabled=not release_context_ready):
                with st.spinner(spinner):
                    try:
                        result = action()
                        ctx.clear_dashboard_cache()
                        st.session_state["last_operation_result"] = ctx.with_actor(result)
                    except Exception as exc:
                        st.session_state["last_operation_result"] = ctx.with_actor({"error": str(exc)})
            st.caption(description)

    st.subheader("Execution Summary")
    ctx.render_operation_result(st.session_state.get("last_operation_result"))
