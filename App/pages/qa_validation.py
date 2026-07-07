from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st

from App.auth import ROLE_ADMIN, ROLE_QA_ENGINEER, current_role, current_user
from App.data_loaders import add_environment_readiness, file_mtime, load_json, normalize_testcase_impact, safe_int
from App.qa_history import (
    append_qa_history,
    build_qa_signoff_history_record,
    calculate_qa_summary,
    executed_count,
    history_dataframe,
    load_qa_history,
    load_release_qa_history,
)
from App.qa_signoff import build_qa_signoff, load_qa_signoff, save_qa_signoff
from App.qa_updates import QAUpdateConflict, build_qa_update_payload, save_qa_row_update
from App.user_store import PERMISSION_QA_SIGNOFF
from App.workspace import active_output_path, active_release_line, active_team_name
from App.workflow_actions import app_state_db_path


def save_qa_manual_update(ctx: Any, 
    software_name: str,
    installation_status: str,
    test_result: str,
    test_case_count: int,
    test_cases_passed: int,
    test_cases_failed: int,
    test_cases_blocked: int,
    notes: str,
    test_date: Any,
    tested_by: str,
    evidence_file: Any | None,
    expected_revision: int,
) -> dict[str, Any]:
    qa_file = active_output_path("qa_validation.json")
    data = load_json(str(qa_file), file_mtime(qa_file))
    if software_name not in data:
        raise ValueError(f"QA record not found for {software_name}")

    record = data[software_name]
    updates = build_qa_update_payload(
        installation_status,
        test_result,
        test_case_count,
        test_cases_passed,
        test_cases_failed,
        test_cases_blocked,
        notes,
        test_date,
        existing_notes=str(record.get("Test Notes") or ""),
    )
    updates["Tested By"] = tested_by.strip() or current_user().get("username", "unknown")
    updated = save_qa_row_update(
        qa_file,
        software_name,
        updates,
        expected_revision=expected_revision,
        updated_by=current_user().get("username", "unknown"),
        evidence_file=evidence_file,
        db_path=app_state_db_path(),
        team=active_team_name(),
        release_line=active_release_line(),
    )
    ctx.clear_dashboard_cache()
    return updated


def can_perform_qa_signoff() -> bool:
    return current_role() == ROLE_ADMIN or PERMISSION_QA_SIGNOFF in current_user().get("permissions", [])


def render_qa_validation(qa_df: pd.DataFrame, ctx: Any) -> None:
    if current_role() not in {ROLE_ADMIN, ROLE_QA_ENGINEER}:
        ctx.render_access_denied("Administrator or QA Engineer")
        return
    ctx.section_title("QA Validation Dashboard", "")
    if qa_df.empty:
        st.info("No QA validation data found. Run version comparison first.")
        return
    qa_df = add_environment_readiness(qa_df)
    columns = [
        "Software Name",
        "Package Version",
        "Deployment Status",
        "Overall QA Result",
        "Environment Readiness",
        "Test Case Count",
        "Test Cases Passed",
        "Test Cases Failed",
        "Test Cases Blocked / Not Tested",
        "Test Cases Executed",
        "Test Case Coverage %",
        "Test Case Source",
        "Test Date",
        "Tested By",
        "Test Notes",
        "Evidence File",
    ]
    testcase_impact_file = active_output_path("testcase_impact.json")
    testcase_impact_excel_file = active_output_path("Test_Case_Impact_Assessment.xlsx")
    impact = load_json(str(testcase_impact_file), file_mtime(testcase_impact_file))
    impacted = impact.get("impacted_software", {}) if isinstance(impact, dict) else {}
    qa_df["Test Case Count"] = qa_df.apply(
        lambda row: safe_int(row.get("Test Case Count") or impacted.get(row["Software Name"], {}).get("Test Case Count", 0)),
        axis=1,
    )
    for field in ["Test Cases Passed", "Test Cases Failed", "Test Cases Blocked / Not Tested", "Test Cases Executed"]:
        qa_df[field] = qa_df[field].map(safe_int)
    qa_df["Test Cases Executed"] = qa_df.apply(executed_count, axis=1)
    qa_df["Test Case Coverage %"] = qa_df.apply(
        lambda row: f"{((safe_int(row['Test Cases Executed']) / safe_int(row['Test Case Count'])) * 100):.1f}%".replace(".0%", "%")
        if safe_int(row["Test Case Count"])
        else "Not Required",
        axis=1,
    )
    qa_df["Test Case Source"] = qa_df["Software Name"].map(lambda name: impacted.get(name, {}).get("Test Coverage", "Not Required"))
    qa_df["Deployment Status"] = qa_df["Installation Status"]
    qa_df["Overall QA Result"] = qa_df["Test Result"]
    qa_summary = calculate_qa_summary(qa_df)
    qa_output_dir = active_output_path("__placeholder__").parent
    latest_signoff = load_qa_signoff(qa_output_dir)

    result_counts = qa_df["Test Result"].value_counts().to_dict()
    validation_cols = st.columns(6)
    validation_cols[0].metric("Total Software", qa_summary["total_software"])
    validation_cols[1].metric("PASS", result_counts.get("PASS", 0))
    validation_cols[2].metric("FAIL", result_counts.get("FAIL", 0))
    validation_cols[3].metric("WARNING", result_counts.get("WARNING", 0))
    validation_cols[4].metric("NOT TESTED", result_counts.get("NOT TESTED", 0))
    validation_cols[5].metric("Last Signoff", latest_signoff.get("status", "Not Signed Off"))
    if latest_signoff:
        st.caption(
            f"Last signed by {latest_signoff.get('signed_by', 'unknown')} on "
            f"{latest_signoff.get('signed_date', 'not available')}."
        )

    st.subheader("QA Test Case Summary")
    testcase_summary_cols = st.columns(6)
    testcase_summary_cols[0].metric("Total Test Cases", qa_summary["total_test_cases"])
    testcase_summary_cols[1].metric("Executed Test Cases", qa_summary["executed_test_cases"])
    testcase_summary_cols[2].metric("Test Case Coverage %", f"{qa_summary['coverage_percent']:g}%")
    testcase_summary_cols[3].metric("Fully Tested", qa_summary["fully_tested"])
    testcase_summary_cols[4].metric("Partially Tested", qa_summary["partially_tested"])
    testcase_summary_cols[5].metric("Not Started", qa_summary["not_tested"])

    ctx.searchable_table(qa_df[columns], "qa_validation", ["Deployment Status", "Overall QA Result", "Environment Readiness"])

    testcase_df = normalize_testcase_impact(impact)
    st.subheader("Recommended Test Cases for Updates")
    st.caption("These are mapped from Input/testcaseRepository.xlsx for software that requires an update.")
    if testcase_df.empty:
        st.info("No recommended test cases found. Run the full pipeline or confirm the testcase repository is available.")
    else:
        metric_cols = st.columns(4)
        metric_cols[0].metric("Software With Updates", int(impact.get("summary", {}).get("software_requiring_update", 0)))
        metric_cols[1].metric("Mapped From Repository", int(impact.get("summary", {}).get("software_with_test_coverage", 0)))
        metric_cols[2].metric("Missing Repository Mapping", int(impact.get("summary", {}).get("software_without_test_coverage", 0)))
        metric_cols[3].metric("Recommended Test Cases", int(impact.get("summary", {}).get("total_recommended_test_cases", 0)))
        display_cols = [
            "Software Name",
            "Test Case Source",
            "Test Case ID",
            "Test Case Name",
            "Test Type",
            "Priority",
            "Automation Status",
            "Owner",
            "Applicable Version",
        ]
        ctx.searchable_table(testcase_df[display_cols], "testcase_impact", ["Test Case Source", "Priority", "Test Type", "Owner"])
        if testcase_impact_excel_file.exists():
            st.download_button(
                "Download Recommended Test Case Plan",
                testcase_impact_excel_file.read_bytes(),
                file_name=testcase_impact_excel_file.name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

    st.subheader("Manual QA Update")
    st.caption("Use this after QA has installed or validated a package. This updates output/qa_validation.json and optionally stores evidence under output/qa_evidence.")
    software_name = st.selectbox("Software", qa_df["Software Name"].tolist(), key="manual_qa_software_selector")
    selected_record = qa_df[qa_df["Software Name"] == software_name].iloc[0]
    selected_key = "".join(ch if ch.isalnum() else "_" for ch in str(software_name))
    installation_options = ["Not Tested", "Installed Successfully", "Failed", "Rollback Completed", "Pending Restart", "No Deployment Required"]
    result_options = ["NOT TESTED", "PASS", "FAIL", "WARNING", "BASELINE VERIFIED"]
    current_installation = str(selected_record.get("Installation Status") or "Not Tested")
    current_result = str(selected_record.get("Test Result") or "NOT TESTED")
    selected_test_case_count = safe_int(selected_record.get("Test Case Count"))
    current_passed = safe_int(selected_record.get("Test Cases Passed"))
    current_failed = safe_int(selected_record.get("Test Cases Failed"))
    current_blocked = safe_int(selected_record.get("Test Cases Blocked / Not Tested"))
    expected_revision = safe_int(selected_record.get("QA Revision"))
    current_executed = safe_int(selected_record.get("Test Cases Executed"))
    if current_executed and not (current_passed or current_failed or current_blocked):
        current_passed = current_executed if current_result in {"PASS", "BASELINE VERIFIED"} else 0
        current_failed = current_executed if current_result == "FAIL" else 0
        current_blocked = current_executed if current_result in {"WARNING", "NOT TESTED"} else 0
    with st.form("manual_qa_update_form"):
        form_cols = st.columns([1, 1])
        installation_status = form_cols[0].selectbox(
            "Installation Status",
            installation_options,
            index=installation_options.index(current_installation) if current_installation in installation_options else 0,
            key=f"qa_installation_{selected_key}",
        )
        test_result = form_cols[1].selectbox(
            "Test Result",
            result_options,
            index=result_options.index(current_result) if current_result in result_options else 0,
            key=f"qa_result_{selected_key}",
        )
        executed_cols = st.columns([1, 1, 1, 1])
        executed_cols[0].number_input("Test Case Count", value=selected_test_case_count, min_value=0, disabled=True, key=f"qa_count_{selected_key}")
        test_cases_passed = executed_cols[1].number_input(
            "Test Cases Passed",
            min_value=0,
            max_value=selected_test_case_count if selected_test_case_count else None,
            value=min(current_passed, selected_test_case_count) if selected_test_case_count else current_passed,
            step=1,
            key=f"qa_passed_{selected_key}",
        )
        remaining_after_pass = max(selected_test_case_count - test_cases_passed, 0) if selected_test_case_count else None
        test_cases_failed = executed_cols[2].number_input(
            "Test Cases Failed",
            min_value=0,
            max_value=remaining_after_pass,
            value=min(current_failed, remaining_after_pass) if remaining_after_pass is not None else current_failed,
            step=1,
            key=f"qa_failed_{selected_key}",
        )
        remaining_after_fail = max(selected_test_case_count - test_cases_passed - test_cases_failed, 0) if selected_test_case_count else None
        test_cases_blocked = executed_cols[3].number_input(
            "Blocked / Not Tested",
            min_value=0,
            max_value=remaining_after_fail,
            value=min(current_blocked, remaining_after_fail) if remaining_after_fail is not None else current_blocked,
            step=1,
            key=f"qa_blocked_{selected_key}",
        )
        test_cases_executed = test_cases_passed + test_cases_failed + test_cases_blocked
        coverage_label = (
            f"{((test_cases_executed / selected_test_case_count) * 100):.1f}%".replace(".0%", "%")
            if selected_test_case_count
            else "Not Required"
        )
        st.caption(f"Calculated test case coverage: {coverage_label}")
        notes = st.text_area(
            "QA Notes",
            value=str(selected_record.get("Test Notes") or ""),
            placeholder="Add install result, validation notes, known issues, or rollback details.",
            key=f"qa_notes_{selected_key}",
        )
        date_cols = st.columns([1, 1, 1])
        test_date = date_cols[0].date_input("Test Date", value=datetime.now().date())
        tested_by = date_cols[1].text_input("Tested By", value=current_user().get("display_name", current_user().get("username", "")))
        evidence_file = date_cols[2].file_uploader("Upload Test Evidence", type=["txt", "log", "csv", "xlsx", "png", "jpg", "jpeg", "pdf"])
        submitted = st.form_submit_button("Save QA Result", type="primary", use_container_width=True)
    if submitted:
        try:
            save_qa_manual_update(
                ctx,
                software_name,
                installation_status,
                test_result,
                selected_test_case_count,
                test_cases_passed,
                test_cases_failed,
                test_cases_blocked,
                notes,
                test_date,
                tested_by,
                evidence_file,
                expected_revision,
            )
            st.success(f"QA result saved for {software_name}.")
            st.rerun()
        except QAUpdateConflict as exc:
            st.warning(str(exc))
        except Exception as exc:
            st.error(f"QA result was not saved: {exc}")

    st.subheader("QA Completion Signoff")
    st.info("QA signoff records validation completion for the selected product and release line.")
    context_cols = st.columns(4)
    context_cols[0].metric("Product", active_team_name())
    context_cols[1].metric("Release Line", active_release_line())
    context_cols[2].metric("Coverage", f"{qa_summary['coverage_percent']:g}%")
    context_cols[3].metric("Not Tested", result_counts.get("NOT TESTED", 0))
    if can_perform_qa_signoff():
        with st.form("qa_completion_signoff_form"):
            signoff_comments = st.text_area("Signoff Comments", placeholder="Summarize validation scope, known gaps, or selective coverage rationale.")
            signoff_by = st.text_input("Signed By", value=current_user().get("display_name", current_user().get("username", "")))
            review_signoff = st.form_submit_button("Review QA Signoff", type="primary", use_container_width=True)
        if review_signoff:
            st.session_state["qa_signoff_pending"] = {
                "comments": signoff_comments,
                "signed_by": signoff_by,
            }
    else:
        review_signoff = False
        st.warning("You can update QA validation, but QA completion signoff requires the QA Signoff permission.")
        st.session_state.pop("qa_signoff_pending", None)

    pending_signoff = st.session_state.get("qa_signoff_pending")
    if pending_signoff:
        st.markdown("#### QA Signoff Confirmation")
        confirm_cols = st.columns(4)
        confirm_cols[0].metric("Product", active_team_name())
        confirm_cols[1].metric("Release Line", active_release_line())
        confirm_cols[2].metric("Executed / Total", f"{qa_summary['executed_test_cases']} / {qa_summary['total_test_cases']}")
        confirm_cols[3].metric("Coverage", f"{qa_summary['coverage_percent']:g}%")
        status_cols = st.columns(4)
        status_cols[0].metric("PASS", result_counts.get("PASS", 0))
        status_cols[1].metric("FAIL", result_counts.get("FAIL", 0))
        status_cols[2].metric("WARNING", result_counts.get("WARNING", 0))
        status_cols[3].metric("NOT TESTED", result_counts.get("NOT TESTED", 0))
        if qa_summary["coverage_percent"] < 100 or result_counts.get("NOT TESTED", 0):
            st.warning(
                "Some QA validation is incomplete. Signoff is allowed, but it will be recorded with warnings when coverage is below 100% or software remains not tested."
            )
        confirm_left, confirm_right = st.columns(2)
        confirm_clicked = confirm_left.button("Confirm QA Signoff", type="primary", use_container_width=True)
        cancel_clicked = confirm_right.button("Cancel Signoff", use_container_width=True)
        if cancel_clicked:
            st.session_state.pop("qa_signoff_pending", None)
            st.rerun()
    else:
        confirm_clicked = False

    if confirm_clicked:
        if not can_perform_qa_signoff():
            st.error("QA signoff was not saved: missing QA Signoff permission.")
            st.session_state.pop("qa_signoff_pending", None)
            st.rerun()
        try:
            signoff = build_qa_signoff(
                active_team_name(),
                active_release_line(),
                qa_df,
                pending_signoff.get("signed_by", ""),
                pending_signoff.get("comments", ""),
            )
            save_qa_signoff(qa_output_dir, signoff)
            append_qa_history(qa_output_dir, build_qa_signoff_history_record(signoff, qa_df))
            st.session_state.pop("qa_signoff_pending", None)
            st.success(f"QA signoff saved: {signoff['status']}.")
            st.rerun()
        except Exception as exc:
            st.error(f"QA signoff was not saved: {exc}")

    st.subheader("QA Signoff History")
    history_rows = load_release_qa_history(qa_output_dir, active_release_line())
    if not history_rows:
        history_rows = load_qa_history(qa_output_dir)
    history_df = history_dataframe(history_rows)
    if history_df.empty:
        st.info("No QA signoff history captured yet.")
    else:
        display_cols = [
            col
            for col in [
                "timestamp",
                "product",
                "release_line",
                "status",
                "executed_test_cases",
                "total_test_cases",
                "coverage_percent",
                "signed_by",
                "comments",
            ]
            if col in history_df.columns
        ]
        display_history = history_df[display_cols].tail(25).rename(
            columns={
                "timestamp": "Timestamp",
                "product": "Product",
                "release_line": "Release Line",
                "status": "Status",
                "executed_test_cases": "Executed Test Cases",
                "total_test_cases": "Total Test Cases",
                "coverage_percent": "Coverage %",
                "signed_by": "Signed By",
                "comments": "Comments",
            }
        )
        st.dataframe(
            display_history,
            use_container_width=True,
            hide_index=True,
            height=min(420, 72 + (len(display_history) * 44)),
            column_config={
                "Timestamp": st.column_config.TextColumn(width="medium"),
                "Product": st.column_config.TextColumn(width="small"),
                "Release Line": st.column_config.TextColumn(width="small"),
                "Status": st.column_config.TextColumn(width="medium"),
                "Executed Test Cases": st.column_config.NumberColumn(width="small"),
                "Total Test Cases": st.column_config.NumberColumn(width="small"),
                "Coverage %": st.column_config.NumberColumn(width="small"),
                "Signed By": st.column_config.TextColumn(width="small"),
                "Comments": st.column_config.TextColumn(width="large"),
            },
        )

