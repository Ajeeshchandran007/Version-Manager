from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from App.auth import ROLE_ADMIN, ROLE_QA_ENGINEER, ROLE_RELEASE_ENGINEER, can_manage_settings, can_run_operations, current_role, current_user, user_team_scope
from App.workspace import (
    CURRENT_RELEASE_DISPLAY_LABEL,
    CURRENT_RELEASE_LABEL,
    DEFAULT_TEAM_LABEL,
    active_config,
    active_output_path,
    active_release_name,
    active_team_name,
    allowed_teams_for_user,
    create_release_snapshot,
    create_team_snapshot,
    list_releases,
    project_path,
    release_display_label,
    release_output_dir,
    release_root,
    release_value_from_display,
    release_name_to_path_name,
)
from Core.notifier import is_actionable_update


def render_context_selector(ctx: Any, location: str = "dashboard") -> None:
    teams = allowed_teams_for_user()
    current_team = active_team_name()
    team_col, release_col = st.columns(2)
    with team_col:
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

    releases = list_releases(selected_team)
    release_options = [CURRENT_RELEASE_LABEL, *releases]
    release_display_options = [release_display_label(release) for release in release_options]
    current_release = active_release_name() if selected_team == current_team else CURRENT_RELEASE_LABEL
    with release_col:
        selected_release_display = st.selectbox(
            "Release",
            release_display_options,
            index=release_options.index(current_release) if current_release in release_options else 0,
            key=f"{location}_release_selector",
        )
        selected_release = release_value_from_display(selected_release_display)

    if selected_team != st.session_state.get("active_team", DEFAULT_TEAM_LABEL) or selected_release != st.session_state.get("active_release", CURRENT_RELEASE_LABEL):
        st.session_state["active_team"] = selected_team
        st.session_state["active_release"] = selected_release
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
        st.metric("Run Context", f"{active_team_name()} / {active_release_name()}")
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
    release = active_release_name()
    title_prefix = "Enterprise" if "*" in user_team_scope() and role == ROLE_ADMIN else team
    subtitle_context = f"{team} / {release_display_label(release)} context"
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
    st.caption(f"Viewing: {active_team_name()} / {release_display_label(active_release_name())}")
    render_dashboard(current_df, comparison_df, vuln_df, metrics_df, ctx)


def _vulnerability_risk(ctx: Any, record: dict[str, Any]) -> str:
    return str(ctx.value(record, "risk_level", "Risk Level", "risk", default="UNKNOWN")).upper()


def release_summary_rows(team: str, ctx: Any) -> list[dict[str, Any]]:
    rows = []
    for release in list_releases(team):
        output = release_root(release, team) / "output"
        comparison = ctx.load_json(str(output / "comparison_report.json"), ctx.file_mtime(output / "comparison_report.json"))
        vulnerabilities = ctx.load_json(str(output / "vulnerability_report.json"), ctx.file_mtime(output / "vulnerability_report.json"))
        readiness = ctx.load_json(str(output / "package_readiness.json"), ctx.file_mtime(output / "package_readiness.json"))
        risky = [
            name for name, record in vulnerabilities.items()
            if _vulnerability_risk(ctx, record) in {"CRITICAL", "HIGH", "MEDIUM"}
        ]
        rows.append(
            {
                "Team": team,
                "Release": release,
                "Software Count": len(comparison) or len(readiness),
                "Updates Required": len([name for name, record in comparison.items() if is_actionable_update(record)]),
                "Security Risk Items": len(risky),
                "Package Records": len(readiness),
                "Last Updated": ctx.format_epoch_ts(max(
                    ctx.file_mtime(output / "comparison_report.json"),
                    ctx.file_mtime(output / "vulnerability_report.json"),
                    ctx.file_mtime(output / "package_readiness.json"),
                    ctx.file_mtime(output / "qa_validation.json"),
                )),
            }
        )
    return rows


def render_release_workspace(config: dict[str, Any], ctx: Any) -> None:
    ctx.section_title("Release Workspace", "Select team context, freeze pre-release work, and manage release-specific assessment outputs.")
    render_context_selector(ctx, "release_workspace")
    selected_team = active_team_name()
    releases = list_releases(selected_team)

    active_cfg = active_config(config)
    input_path = project_path(active_cfg.get("input_files", {}).get("software_yml", "Input/software.yml"))
    output_path = active_output_path("comparison_report.json").parent
    metric_cols = st.columns(4)
    metric_cols[0].metric("Active Team", active_team_name())
    metric_cols[1].metric("Active Release", release_display_label(active_release_name()))
    metric_cols[2].metric("Known Releases", len(releases))
    metric_cols[3].metric("Input Exists", "Yes" if input_path.exists() else "No")

    st.markdown("**Active Paths**")
    st.code(f"Input:  {input_path}\nOutput: {output_path}", language="text")

    if can_manage_settings():
        with st.expander("+ Add New Team", expanded=False):
            st.caption("Use this only when the team/product stream is not already listed above.")
            team_cols = st.columns([1, 1])
            with team_cols[0]:
                new_team = st.text_input("New Team Name", placeholder="CyberRecovery")
            with team_cols[1]:
                st.write("")
                st.write("")
                create_team_clicked = st.button("Create Team From Current Input", use_container_width=True)
            if create_team_clicked:
                ok, message = create_team_snapshot(new_team, config)
                if ok:
                    ctx.clear_dashboard_cache()
                    st.success(message)
                    st.rerun()
                else:
                    st.error(message)

    if current_role() in {ROLE_ADMIN, ROLE_RELEASE_ENGINEER}:
        st.subheader("Freeze Pre-Release Work as Release")
        st.caption("Use this after package readiness and QA validation are complete enough to save a release baseline.")
        form_cols = st.columns([1, 1, 1])
        with form_cols[0]:
            new_release = st.text_input("New Release Name", placeholder="7.2.11")
        with form_cols[1]:
            base_options = [CURRENT_RELEASE_LABEL, *releases]
            base_release_display = st.selectbox("Base From", [release_display_label(release) for release in base_options], key="release_workspace_base")
            base_release = release_value_from_display(base_release_display)
        with form_cols[2]:
            st.write("")
            st.write("")
            create_clicked = st.button("Freeze as Release", type="primary", use_container_width=True)
        if create_clicked:
            release_to_create = release_name_to_path_name(new_release)
            if not release_to_create:
                st.error("Enter a release name such as 7.2.11.")
            else:
                st.session_state["pending_release_freeze"] = {
                    "team": selected_team,
                    "release": release_to_create,
                    "base_release": base_release,
                }

        pending_freeze = st.session_state.get("pending_release_freeze")
        if pending_freeze:
            team = pending_freeze["team"]
            release = pending_freeze["release"]
            base_release = pending_freeze["base_release"]
            target_path = release_root(release, team)
            st.warning(
                "You are about to freeze the current team work into a release baseline. "
                "Use this only after package readiness and QA validation are complete enough for release tracking."
            )
            st.markdown(
                f"""
                **Current work area:** `{team} / {release_display_label(base_release)}`

                **New release:** `{team} / {release}`

                **Release folder:** `{target_path}`
                """
            )
            confirm_cols = st.columns(2)
            with confirm_cols[0]:
                if st.button("Cancel", use_container_width=True):
                    st.session_state.pop("pending_release_freeze", None)
                    st.rerun()
            with confirm_cols[1]:
                if st.button("Confirm Freeze as Release", type="primary", use_container_width=True):
                    if current_role() not in {ROLE_ADMIN, ROLE_RELEASE_ENGINEER}:
                        ok, message = False, "Only Release Engineer or Admin can freeze a release."
                    else:
                        ok, message = create_release_snapshot(release, base_release, config, team)
                    st.session_state.pop("pending_release_freeze", None)
                    if ok:
                        ctx.clear_dashboard_cache()
                        st.success(message)
                        st.rerun()
                    else:
                        st.error(message)
    elif st.session_state.get("pending_release_freeze"):
        st.session_state.pop("pending_release_freeze", None)

    rows = release_summary_rows(active_team_name(), ctx)
    st.subheader("Release Baselines")
    if rows:
        ctx.searchable_table(pd.DataFrame(rows), "release_workspace_summary", ["Release"])
    else:
        st.info("No release baselines found for this team yet.")


def release_freeze_status(
    comparison_df: pd.DataFrame,
    readiness_df: pd.DataFrame,
    qa_df: pd.DataFrame,
    ctx: Any,
) -> dict[str, Any]:
    blocked_packages = int((readiness_df.get("Package Readiness", pd.Series(dtype=str)) == "Blocked").sum()) if not readiness_df.empty else 0
    failed_tests = int((qa_df.get("Test Result", pd.Series(dtype=str)) == "FAIL").sum()) if not qa_df.empty else 0
    not_tested = int((qa_df.get("Test Result", pd.Series(dtype=str)) == "NOT TESTED").sum()) if not qa_df.empty else 0
    source_review = int((comparison_df.get("Status", pd.Series(dtype=str)) == "Source Review").sum()) if not comparison_df.empty else 0
    test_plan = active_output_path("Test_Case_Impact_Assessment.xlsx")
    signoffs = load_release_signoffs()

    gates = [
        {
            "Key": "release_package",
            "Gate": "Release Package Review",
            "Owner": "Release Engineer",
            "Evidence Status": "Ready" if not readiness_df.empty and blocked_packages == 0 else "Action Required",
            "Evidence": f"{len(readiness_df)} package record(s); {blocked_packages} blocked item(s)",
        },
        {
            "Key": "qa_validation",
            "Gate": "QA Validation",
            "Owner": "QA Engineer",
            "Evidence Status": "Ready" if not qa_df.empty and failed_tests == 0 and not_tested == 0 else "Action Required",
            "Evidence": f"{len(qa_df)} QA record(s); {failed_tests} failed, {not_tested} not tested",
        },
        {
            "Key": "security_source_review",
            "Gate": "Version Source Review",
            "Owner": "Release Engineer",
            "Evidence Status": "Ready" if source_review == 0 else "Action Required",
            "Evidence": f"{source_review} source-review item(s)",
        },
        {
            "Key": "recommended_test_plan",
            "Gate": "Recommended Test Plan",
            "Owner": "QA Engineer",
            "Evidence Status": "Ready" if test_plan.exists() else "Action Required",
            "Evidence": str(test_plan),
        },
    ]
    for gate in gates:
        signoff = signoffs.get(gate["Key"], {})
        gate["Sign-off"] = "Signed Off" if signoff else "Pending"
        gate["Signed By"] = signoff.get("display_name", "")
        gate["Signed At"] = signoff.get("signed_at", "")
        gate["Status"] = "Ready" if gate["Evidence Status"] == "Ready" and signoff else "Action Required"

    ready = all(gate["Status"] == "Ready" for gate in gates)
    return {
        "ready": ready,
        "summary": "Ready to Freeze" if ready else "Not Ready to Freeze",
        "gates": gates,
        "signoffs": signoffs,
    }


def load_release_signoffs() -> dict[str, Any]:
    path = active_output_path("release_freeze_signoffs.json")
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_release_signoff(key: str) -> None:
    path = active_output_path("release_freeze_signoffs.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    signoffs = load_release_signoffs()
    user = current_user()
    signoffs[key] = {
        "username": user.get("username", "unknown"),
        "display_name": user.get("display_name", user.get("username", "unknown")),
        "role": current_role(),
        "team": active_team_name(),
        "release": active_release_name(),
        "signed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(signoffs, indent=2), encoding="utf-8")


def can_sign_gate(key: str) -> bool:
    role = current_role()
    if role == ROLE_ADMIN:
        return True
    if key in {"release_package", "security_source_review"}:
        return role == ROLE_RELEASE_ENGINEER
    if key in {"qa_validation", "recommended_test_plan"}:
        return role == ROLE_QA_ENGINEER
    return False


def render_release_freeze_status(
    config: dict[str, Any],
    comparison_df: pd.DataFrame,
    readiness_df: pd.DataFrame,
    qa_df: pd.DataFrame,
    ctx: Any,
) -> None:
    ctx.section_title("Release Freeze Status", "Shared release gate for Release Engineering and QA validation readiness.")
    render_context_selector(ctx, "release_freeze_status")
    status = release_freeze_status(comparison_df, readiness_df, qa_df, ctx)

    cols = st.columns(4)
    cols[0].metric("Team", active_team_name())
    cols[1].metric("Release Context", release_display_label(active_release_name()))
    cols[2].metric("Freeze Status", status["summary"])
    open_gates = [gate for gate in status["gates"] if gate["Status"] != "Ready"]
    cols[3].metric("Open Gates", len(open_gates))

    if status["ready"]:
        st.success("This release context is ready to freeze. Admin or Release Engineer can freeze it from Release Workspace.")
    else:
        st.warning("This release context is not ready to freeze. Complete the action-required gates below.")

    gate_df = pd.DataFrame(status["gates"]).drop(columns=["Key"], errors="ignore")
    ctx.searchable_table(gate_df, "release_freeze_status", ["Owner", "Status"])

    st.subheader("Gate Sign-offs")
    sign_cols = st.columns(4)
    for col, gate in zip(sign_cols, status["gates"]):
        with col:
            st.markdown(f"**{gate['Gate']}**")
            st.caption(gate["Owner"])
            st.caption(gate["Evidence"])
            if gate["Sign-off"] == "Signed Off":
                st.success(f"Signed by {gate['Signed By']}")
            elif gate["Evidence Status"] != "Ready":
                st.warning("Evidence not ready")
            elif can_sign_gate(gate["Key"]):
                if st.button(f"Sign off {gate['Owner']}", key=f"signoff_{gate['Key']}", use_container_width=True):
                    save_release_signoff(gate["Key"])
                    st.rerun()
            else:
                st.info("Waiting for owner sign-off")

    action_rows = open_gates
    if action_rows:
        st.subheader("Open Actions")
        st.dataframe(ctx.style_operational_table(pd.DataFrame(action_rows).drop(columns=["Key"], errors="ignore")), use_container_width=True, hide_index=True)

    st.subheader("Final Freeze")
    if active_release_name() != CURRENT_RELEASE_LABEL:
        st.info(f"{release_display_label(active_release_name())} is already a frozen release context.")
    elif current_role() not in {ROLE_ADMIN, ROLE_RELEASE_ENGINEER}:
        st.info("Final freeze is owned by Release Engineer or Admin after all gates are signed off.")
    elif not status["ready"]:
        st.warning("Final freeze is disabled until every evidence gate is ready and signed off.")
    else:
        freeze_cols = st.columns([1, 1])
        release_name = freeze_cols[0].text_input("Release Name To Freeze", placeholder="7.2.12")
        with freeze_cols[1]:
            st.write("")
            st.write("")
            if st.button("Freeze Signed-off Release", type="primary", use_container_width=True):
                ok, message = create_release_snapshot(release_name, CURRENT_RELEASE_LABEL, config, active_team_name())
                if ok:
                    st.success(message)
                    st.rerun()
                else:
                    st.error(message)


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
