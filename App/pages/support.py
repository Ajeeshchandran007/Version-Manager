from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from App.auth import ROLE_ADMIN, ROLE_QA_ENGINEER, ROLE_RELEASE_ENGINEER, current_role, current_user
from App.data_loaders import compliance_score
from App.workspace import active_output_path, active_release_line, active_team_name


RISK_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE", "UNKNOWN"]


def posture_label(score: int, updates: int, critical: int, high: int) -> tuple[str, str]:
    if critical or high:
        return "Security Attention Required", "bad"
    if updates:
        return "Maintenance Required", "warn"
    if score >= 95:
        return "Healthy", "ok"
    return "Monitor", "info"


def render_posture_strip(comparison_df: pd.DataFrame, vuln_df: pd.DataFrame) -> None:
    total = len(comparison_df)
    updates = int((comparison_df["Need Update"] == "Yes").sum()) if not comparison_df.empty else 0
    score = compliance_score(comparison_df)
    risk_counts = vuln_df["Risk Level"].value_counts().to_dict() if not vuln_df.empty else {}
    posture, tone = posture_label(score, updates, risk_counts.get("CRITICAL", 0), risk_counts.get("HIGH", 0))
    highest = next((risk for risk in RISK_ORDER if risk_counts.get(risk, 0)), "NONE")
    st.markdown(
        f"""
        <div class="vm-posture">
            <div class="vm-posture-item primary">
                <div class="vm-posture-label">Overall Posture</div>
                <div class="vm-posture-value">{posture}</div>
                <div class="vm-posture-note">{updates} of {total} applications require updates</div>
            </div>
            <div class="vm-posture-item">
                <div class="vm-posture-label">Compliance Score</div>
                <div class="vm-posture-value">{score}%</div>
                <div class="vm-posture-note">Version compliance against latest catalog</div>
            </div>
            <div class="vm-posture-item">
                <div class="vm-posture-label">Highest Security Risk</div>
                <div class="vm-posture-value">{highest.title()}</div>
                <div class="vm-posture-note">{risk_counts.get(highest, 0)} item(s) at this level</div>
            </div>
            <div class="vm-posture-item">
                <div class="vm-posture-label">Update Exposure</div>
                <div class="vm-posture-value">{updates}</div>
                <div class="vm-posture-note">Open remediation candidates</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )



def visible_output_files_for_role(role: str, include_operational_reports: bool = True) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = [("Management Report - HTML", active_output_path("email_preview.html"))]
    if role in {ROLE_ADMIN, ROLE_RELEASE_ENGINEER, ROLE_QA_ENGINEER}:
        files.append(("Technical Report - Excel", active_output_path("Software_Version_Assessment.xlsx")))
    if role in {ROLE_ADMIN, ROLE_RELEASE_ENGINEER}:
        files.append(("Package Readiness Data", active_output_path("package_readiness.json")))
    if role == ROLE_ADMIN:
        files.append(("QA Validation Data", active_output_path("qa_validation.json")))
    if role in {ROLE_ADMIN, ROLE_RELEASE_ENGINEER, ROLE_QA_ENGINEER}:
        files.append(("Test Case Impact Plan", active_output_path("Test_Case_Impact_Assessment.xlsx")))
    if include_operational_reports and role == ROLE_ADMIN:
        files.extend([
            ("Comparison Report", active_output_path("comparison_report.json")),
            ("Vulnerability Report", active_output_path("vulnerability_report.json")),
            ("Test Case Impact Data", active_output_path("testcase_impact.json")),
        ])
    return files



def render_operation_result(result: dict[str, Any] | None) -> None:
    if not result:
        st.info("No operation has been run in this session.")
        return
    if result.get("error"):
        st.error("Operation failed. Review the message below and correct the configuration or input data.")
        st.code(str(result["error"]))
        return

    operation = result.get("operation", "operation")
    actor = result.get("triggered_by") or current_user().get("username", "unknown")
    cards: list[tuple[str, Any, str]] = []
    title = "Operation Completed"
    summary = "The selected operation finished successfully."
    next_action = "Review the refreshed dashboard pages."

    if operation in {"full_pipeline", "shared_workflow", "package_workflow", "qa_workflow"}:
        if operation == "shared_workflow":
            title = "Shared Scan Completed"
            summary = "Latest versions, current inventory, version comparison, and compatibility data were refreshed. Package and security-owned outputs were not updated."
        elif current_role() == ROLE_QA_ENGINEER:
            title = "Validation Workflow Completed"
            summary = "Shared scan outputs and QA validation outputs were refreshed by the controlled backend workflow."
        elif operation == "package_workflow":
            title = "Package Workflow Completed"
            summary = "Shared scan outputs and package readiness outputs were refreshed for the selected release."
        else:
            title = "Full Pipeline Completed"
            summary = "Latest versions, current inventory, comparison, vulnerability assessment, Excel output, and email notification were processed."
        cards = [
            ("Team / Product", result.get("active_team", active_team_name()), "Workflow execution context"),
            ("Release Line", result.get("active_release", active_release_line()), "Selected product version"),
            ("Agent Verification", "Passed" if result.get("verification_result", {}).get("passed", True) else "Review", "Planner/Verifier quality gate result"),
            ("Applications Checked", result.get("total", 0), "Software records processed"),
            ("Updates Required", len(result.get("needs_update", [])), "Applications needing remediation"),
            ("Email Sent", "Yes" if result.get("email_sent") else "No", "Notification delivery status"),
            ("Data Mode", "Fresh Data" if result.get("cache_mode") == "fresh" else "Cache Enabled", "Whether the run used cache or requested fresh data"),
        ]
        if result.get("email_sent"):
            next_action = "Open Dashboard or Reports to review the assessment package."
        else:
            next_action = "Review SMTP settings or approval configuration before sending email."
    elif operation == "fetch_latest_versions":
        title = "Latest Version Catalog Refreshed"
        summary = "The approved latest-version catalog was updated for the selected software category."
        cards = [
            ("Applications Updated", result.get("total", 0), "Latest-version records refreshed"),
            ("Data Mode", "Fresh Data" if result.get("cache_mode") == "fresh" else "Cache Enabled", "Whether the lookup used cache or requested fresh data"),
        ]
        next_action = "Run Compare Versions after current inventory is available."
    elif operation == "fetch_current_versions":
        title = "Current Inventory Refreshed"
        summary = "Installed versions were resolved from configured servers with document fallback where needed."
        cards = [
            ("Applications Checked", result.get("total", 0), "Current-version records refreshed"),
            ("Live Server Results", result.get("from_server", 0), "Resolved from configured servers"),
            ("Document Fallback", result.get("from_document", 0), "Resolved from PDF inventory"),
        ]
        next_action = "Run Compare Versions after latest-version data is available."
    elif operation == "compare_versions":
        title = "Version Comparison Completed"
        summary = "Current versions were compared against the latest-version catalog."
        cards = [
            ("Applications Compared", result.get("total", 0), "Software records compared"),
            ("Updates Required", result.get("needs_update", 0), "Applications behind latest version"),
        ]
        next_action = "Open Version Comparison or send the report email."
    elif operation == "send_report_email":
        sent = bool(result.get("sent"))
        title = "Email Report Sent" if sent else "Email Report Was Not Sent"
        summary = "The version assessment email was submitted to the configured SMTP service." if sent else "The email could not be delivered. Check SMTP settings and recipient configuration."
        cards = [
            ("Delivery Status", "Sent" if sent else "Failed", "SMTP result"),
            ("Recipients", result.get("recipients", 0), "Configured recipient count"),
            ("Subject", result.get("subject", "Not available"), "Email subject"),
        ]
        next_action = "Confirm delivery with the recipients." if sent else "Open Settings and verify SMTP configuration."
    st.markdown(
        f"""
        <div class="vm-card">
            <strong>{title}</strong>
            <div class="vm-posture-note">{summary}</div>
            <div class="vm-posture-note">Triggered by: {actor}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if cards:
        for start in range(0, len(cards), 4):
            row_cards = cards[start:start + 4]
            cols = st.columns(len(row_cards))
            for col, (label, val, help_text) in zip(cols, row_cards):
                col.metric(label, val, help=help_text)

    st.info(f"Next recommended action: {next_action}")
    if not result.get("email_sent", True) and result.get("email_error"):
        st.warning(f"Email was not sent: {result.get('email_error')}")

    available_files = visible_output_files_for_role(current_role())
    existing_files = [(label, path) for label, path in available_files if path.exists()]
    if existing_files:
        st.markdown("**Available outputs**")
        file_cols = st.columns(min(len(existing_files), 4))
        for col, (label, path) in zip(file_cols, existing_files):
            with col:
                st.caption(label)
                st.download_button(
                    "Download",
                    path.read_bytes(),
                    file_name=path.name,
                    use_container_width=True,
                )

    with st.expander("Technical details"):
        st.json(result, expanded=False)


