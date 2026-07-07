from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from App.workspace import active_release_line, active_team_name
from App.workflow_runs import list_workflow_runs


def performance_event(metric: str) -> tuple[str, str, str]:
    mapping = {
        "fetch_latest_versions.duration_ms": (
            "Latest Version Discovery",
            "Collected latest vendor versions and release metadata.",
            "Research",
        ),
        "fetch_current_versions.duration_ms": (
            "Current Inventory Check",
            "Collected installed versions from configured servers or document fallback.",
            "Inventory",
        ),
        "compare_versions.duration_ms": (
            "Version Comparison",
            "Compared installed versions against latest approved versions.",
            "Compliance",
        ),
        "check_vulnerabilities.duration_ms": (
            "Vulnerability Check",
            "Checked current installed versions against vulnerability data.",
            "Security",
        ),
        "send_notification.duration_ms": (
            "Email Report Delivery",
            "Generated and sent the version assessment email report.",
            "Notification",
        ),
    }
    return mapping.get(metric, ("Workflow Step", "Recorded a workflow processing step.", "System"))


def build_performance_metrics(metrics_df: pd.DataFrame, ctx: Any) -> pd.DataFrame:
    rows = []
    if metrics_df.empty:
        return pd.DataFrame()
    for _, item in metrics_df.tail(50).iterrows():
        labels = item.get("labels", {})
        if not isinstance(labels, dict):
            labels = {}
        stage, purpose, category = performance_event(str(item.get("metric", "")))
        rows.append(
            {
                "Stage": stage,
                "Category": category,
                "Status": str(labels.get("status", "ok")).title(),
                "Duration": ctx.format_duration_ms(item.get("value")),
                "Items Processed": str(labels.get("total", "Not applicable")),
                "Completed At": ctx.format_ts(str(item.get("ts", ""))),
                "Purpose": purpose,
                "Trace ID": item.get("trace_id", ""),
                "Technical Metric": item.get("metric", ""),
            }
        )
    return pd.DataFrame(rows)


def render_workflow(metrics_df: pd.DataFrame, ctx: Any) -> None:
    ctx.section_title("Workflow Monitor", "Pipeline execution stages and processing duration.")
    st.subheader("Workflow Run History")
    try:
        run_rows = list_workflow_runs(
            ctx.app_state_db_path(active_team_name(), active_release_line()),
            team=active_team_name(),
            release_line=active_release_line(),
            limit=25,
        )
    except Exception as exc:
        run_rows = []
        st.warning(f"Workflow run history is not available: {exc}")
    if run_rows:
        run_df = pd.DataFrame(
            [
                {
                    "Run ID": row.get("run_id", ""),
                    "Team": row.get("team", ""),
                    "Release": row.get("release_line", ""),
                    "Scope": str(row.get("workflow_scope", "")).title(),
                    "Status": str(row.get("status", "")).title(),
                    "Triggered By": row.get("triggered_by", ""),
                    "Role": row.get("triggered_by_role", ""),
                    "Started": ctx.format_ts(str(row.get("started_at", ""))),
                    "Duration Seconds": row.get("duration_seconds", ""),
                    "Total": row.get("total", 0),
                    "Needs Update": row.get("needs_update_count", 0),
                    "Unknown": row.get("unknown_count", 0),
                    "Email Sent": "Yes" if row.get("email_sent") else "No",
                    "Error": row.get("error_message", ""),
                }
                for row in run_rows
            ]
        )
        st.dataframe(ctx.style_operational_table(run_df), use_container_width=True, hide_index=True)
    else:
        st.info("No persisted workflow runs found for the selected team and release.")

    nodes = [
        ("Planner Agent", "Selects next required workflow step"),
        ("Discovery Agent", "Inventory collection"),
        ("Research Agent", "Latest versions"),
        ("Analysis Agent", "Version comparison"),
        ("Security Agent", "CVE assessment"),
        ("Package Readiness Agent", "Release/package readiness"),
        ("Compatibility Agent", "Compatibility review"),
        ("QA Validation Agent", "QA plan and test impact"),
        ("Verifier Agent", "Checks outputs and prevents loops"),
        ("Reporting Agent", "Reports and email"),
    ]
    st.markdown(
        '<div class="vm-flow">'
        + "".join(
            f'<div class="vm-node"><strong>{name}</strong><span>{summary}</span></div>'
            for name, summary in nodes
        )
        + "</div>",
        unsafe_allow_html=True,
    )

    st.subheader("Agent Output Summary")
    agent_rows = [
        {"Agent": "Planner Agent", "Status": "Active", "Output Summary": "Routes workflow by missing required state outputs."},
        {"Agent": "Discovery Agent", "Status": "Completed", "Output Summary": "Current software inventory loaded."},
        {"Agent": "Research Agent", "Status": "Completed", "Output Summary": "Latest vendor release catalog generated."},
        {"Agent": "Analysis Agent", "Status": "Completed", "Output Summary": "Version compliance and update status calculated."},
        {"Agent": "Security Agent", "Status": "Completed", "Output Summary": "NVD vulnerability assessment completed."},
        {"Agent": "Package Readiness Agent", "Status": "Completed", "Output Summary": "Package readiness output generated."},
        {"Agent": "Compatibility Agent", "Status": "Completed", "Output Summary": "Compatibility requirements checked."},
        {"Agent": "QA Validation Agent", "Status": "Completed", "Output Summary": "QA validation and test case impact generated."},
        {"Agent": "Verifier Agent", "Status": "Active", "Output Summary": "Verifies expected outputs and fails closed after retry limit."},
        {"Agent": "Reporting Agent", "Status": "Completed", "Output Summary": "Excel, email preview, and notifications prepared."},
    ]
    st.dataframe(ctx.style_operational_table(pd.DataFrame(agent_rows)), use_container_width=True, hide_index=True)

    if run_rows:
        latest_summary = run_rows[0].get("summary", {}) if isinstance(run_rows[0].get("summary", {}), dict) else {}
        workflow_plan = latest_summary.get("workflow_plan", {})
        verification = latest_summary.get("verification_result", {})
        retries = latest_summary.get("verification_retries", {})
        if workflow_plan or verification:
            st.subheader("Planner / Verifier Check")
            check_rows = [
                {
                    "Check": "Latest Planner Decision",
                    "Result": workflow_plan.get("next_agent", "Completed"),
                    "Details": workflow_plan.get("reason", "All required workflow outputs are present."),
                },
                {
                    "Check": "Verifier Status",
                    "Result": "Passed" if verification.get("passed", True) else "Review Required",
                    "Details": verification.get("reason", "Specialist outputs verified."),
                },
                {
                    "Check": "Missing Outputs",
                    "Result": ", ".join(verification.get("missing_outputs", []) or []) or "None",
                    "Details": "Fields expected by the verifier for the last specialist step.",
                },
                {
                    "Check": "Retry Counts",
                    "Result": str(retries or {}),
                    "Details": "Bounded retry counter; prevents infinite routing loops.",
                },
            ]
            st.dataframe(ctx.style_operational_table(pd.DataFrame(check_rows)), use_container_width=True, hide_index=True)

    st.subheader("Pipeline Performance")
    performance_df = build_performance_metrics(metrics_df, ctx)
    if performance_df.empty:
        st.info("No workflow performance data found.")
    else:
        latest_trace = ""
        if "Trace ID" in performance_df and not performance_df["Trace ID"].dropna().empty:
            latest_trace = str(performance_df["Trace ID"].dropna().iloc[-1])
        latest_run = performance_df[performance_df["Trace ID"] == latest_trace] if latest_trace else performance_df.tail(6)
        display_cols = ["Stage", "Category", "Status", "Duration", "Items Processed", "Completed At", "Purpose"]
        st.dataframe(ctx.style_operational_table(latest_run[display_cols]), use_container_width=True, hide_index=True)
        with st.expander("Technical performance details"):
            technical_cols = ["Completed At", "Technical Metric", "Trace ID", "Status", "Duration"]
            st.dataframe(performance_df[technical_cols].tail(20), use_container_width=True, hide_index=True)
