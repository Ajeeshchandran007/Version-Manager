from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any
from types import SimpleNamespace

import pandas as pd
import streamlit as st
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from App.assistant_chat import ROLE_ASSISTANT_PAGES, render_ai_assistant as render_assistant_chat
from App import workflow_ui
from App.pages import admin as admin_pages
from App.pages import qa_validation as qa_validation_page
from App.pages import release_readiness_gate as release_readiness_gate_page
from App.pages import security as security_page
from App.pages import vulnerability_war_room as vulnerability_war_room_page
from App.pages.support import render_operation_result, render_posture_strip, visible_output_files_for_role
from App.data_loaders import (
    add_environment_readiness,
    build_transient_compatibility_df,
    compliance_score,
    configured_inventory_from_active_software_yml,
    describe_cron,
    file_mtime,
    last_scan_time,
    load_file_text,
    load_json,
    load_metrics,
    load_vendor_compatibility_requirements,
    load_workspace_outputs,
    next_scan_time,
    normalize_comparison,
    normalize_current,
    normalize_latest,
    normalize_package_readiness,
    normalize_qa_validation,
    normalize_vulnerabilities,
    value,
)
from App.formatting import format_duration_ms, format_epoch_ts, format_ts
from App.layout import inject_css, render_access_denied, render_app_header, section_title
from App.navigation import pages_for_role, render_sidebar
from App.ui_components import bar_chart, donut_chart, searchable_table, style_operational_table
from App.workflow_actions import (
    app_state_db_path,
    run_async,
    trigger_compare_versions,
    trigger_fetch_current_versions,
    trigger_fetch_latest_versions,
    trigger_full_pipeline,
    trigger_package_workflow,
    trigger_qa_workflow,
    trigger_send_report_email,
    trigger_shared_scan,
)
from App.auth import (
    ROLE_ADMIN,
    ROLE_QA_ENGINEER,
    ROLE_RELEASE_ENGINEER,
    current_role,
    current_user,
    require_login,
)
from App import pages as app_pages
from App.workspace import (
    BASE_DIR,
    OUTPUT_DIR,
    active_config,
    allowed_teams_for_user,
    project_path,
)
from Utils.utils import load_config


OUTPUT_DIR = BASE_DIR / "output"
CACHE_DIR = OUTPUT_DIR / "cache"

CURRENT_FILE = OUTPUT_DIR / "current_versions.json"
LATEST_FILE = OUTPUT_DIR / "latest_versions.json"
COMPARISON_FILE = OUTPUT_DIR / "comparison_report.json"
VULNERABILITY_FILE = OUTPUT_DIR / "vulnerability_report.json"
PACKAGE_READINESS_FILE = OUTPUT_DIR / "package_readiness.json"
QA_VALIDATION_FILE = OUTPUT_DIR / "qa_validation.json"
TESTCASE_IMPACT_FILE = OUTPUT_DIR / "testcase_impact.json"
TESTCASE_IMPACT_EXCEL_FILE = OUTPUT_DIR / "Test_Case_Impact_Assessment.xlsx"
QA_EVIDENCE_DIR = OUTPUT_DIR / "qa_evidence"
CACHE_METRICS_FILE = CACHE_DIR / "cache_metrics.json"
METRICS_FILE = OUTPUT_DIR / "metrics.jsonl"
CONFIG_FILE = BASE_DIR / "config.json"

EXCEL_FILE = OUTPUT_DIR / "Software_Version_Assessment.xlsx"
EMAIL_HTML_FILE = OUTPUT_DIR / "email_preview.html"
EMAIL_TEXT_FILE = OUTPUT_DIR / "email_preview.txt"

ACTION_ROLES = {ROLE_ADMIN, ROLE_RELEASE_ENGINEER, ROLE_QA_ENGINEER}
ADMIN_ROLES = {ROLE_ADMIN}
BASE_PAGES = [
    "Dashboard",
    "Software Inventory",
    "Operations",
    "Latest Versions",
    "Version Comparison",
    "Compatibility Review",
    "Reports",
]
WORKFLOW_MONITOR_PAGE = "Workflow Monitor"
SECURITY_PAGES = ["Vulnerability Assessment", "Vulnerability Evidence", "Release Decision Gate", "Cache Analytics"]
CACHE_PAGES = ["Cache Analytics"]
RELEASE_PAGES = ["Package Readiness"]
QA_PAGES = ["QA Validation"]
ADMIN_PAGES = ["Audit Logs", "Admin User Management", "Settings"]

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
RISK_COLORS = {
    "CRITICAL": "#ef4444",
    "HIGH": "#f97316",
    "MEDIUM": "#f59e0b",
    "LOW": "#22c55e",
    "NONE": "#38bdf8",
    "UNKNOWN": "#94a3b8",
}


st.set_page_config(
    page_title="Enterprise Product Release AI Advisory Platform",
    page_icon="VM",
    layout="wide",
    initial_sidebar_state="expanded",
)


def clear_dashboard_cache() -> None:
    load_json.clear()
    load_metrics.clear()
    load_file_text.clear()




@st.cache_resource
def app_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=datetime.now().astimezone().tzinfo)
    scheduler.start()
    return scheduler


def scheduled_background_scan(category: str = "ALL") -> None:
    try:
        asyncio.run(trigger_full_pipeline(category, force_refresh=False))
    except Exception as exc:
        print(f"Scheduled background scan failed: {exc}")


def validate_cron_expression(schedule: str) -> tuple[bool, str]:
    try:
        CronTrigger.from_crontab(schedule)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def save_schedule_config(schedule: str) -> dict[str, Any]:
    if CONFIG_FILE.exists():
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    else:
        config = load_config()
    config["schedule_cron"] = schedule
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")
    clear_dashboard_cache()
    return config


def apply_background_schedule(schedule: str, category: str) -> str:
    scheduler = app_scheduler()
    trigger = CronTrigger.from_crontab(schedule)
    scheduler.add_job(
        scheduled_background_scan,
        trigger,
        args=[category],
        id="streamlit_version_pipeline",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    job = scheduler.get_job("streamlit_version_pipeline")
    if job and job.next_run_time:
        return job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
    return "Scheduled"


def sync_background_schedule_from_config(config: dict[str, Any]) -> None:
    """Keep the Streamlit background scheduler aligned with config.json edits."""
    schedule = config.get("schedule_cron", "")
    category = config.get("default_category", "ALL")
    signature = f"{schedule}|{category}|{file_mtime(CONFIG_FILE)}"
    if st.session_state.get("active_schedule_signature") == signature:
        return

    scheduler = app_scheduler()
    if not schedule:
        job = scheduler.get_job("streamlit_version_pipeline")
        if job:
            scheduler.remove_job("streamlit_version_pipeline")
        st.session_state["active_schedule_signature"] = signature
        st.session_state["active_schedule_description"] = "Not configured"
        return

    try:
        next_run = apply_background_schedule(schedule, category)
        st.session_state["active_schedule_signature"] = signature
        st.session_state["active_schedule_description"] = next_run
    except Exception as exc:
        st.session_state["active_schedule_error"] = str(exc)


def with_actor(result: dict[str, Any]) -> dict[str, Any]:
    user = current_user()
    result["triggered_by"] = user.get("username", "unknown")
    result["triggered_by_role"] = current_role()
    result["triggered_at"] = datetime.now().isoformat(timespec="seconds")
    return result




def page_context() -> SimpleNamespace:
    return SimpleNamespace(
        add_environment_readiness=add_environment_readiness,
        app_state_db_path=app_state_db_path,
        apply_background_schedule=apply_background_schedule,
        bar_chart=bar_chart,
        clear_dashboard_cache=clear_dashboard_cache,
        compliance_score=compliance_score,
        configured_inventory_from_active_software_yml=configured_inventory_from_active_software_yml,
        describe_cron=describe_cron,
        donut_chart=donut_chart,
        file_mtime=file_mtime,
        format_duration_ms=format_duration_ms,
        format_epoch_ts=format_epoch_ts,
        format_ts=format_ts,
        load_file_text=load_file_text,
        load_json=load_json,
        render_access_denied=render_access_denied,
        render_operation_result=render_operation_result,
        render_posture_strip=render_posture_strip,
        run_async=run_async,
        save_schedule_config=save_schedule_config,
        searchable_table=searchable_table,
        section_title=section_title,
        style_operational_table=style_operational_table,
        trigger_compare_versions=trigger_compare_versions,
        trigger_fetch_current_versions=trigger_fetch_current_versions,
        trigger_fetch_latest_versions=trigger_fetch_latest_versions,
        trigger_full_pipeline=trigger_full_pipeline,
        trigger_package_workflow=trigger_package_workflow,
        trigger_qa_workflow=trigger_qa_workflow,
        trigger_shared_scan=trigger_shared_scan,
        trigger_send_report_email=trigger_send_report_email,
        validate_cron_expression=validate_cron_expression,
        value=value,
        visible_output_files_for_role=visible_output_files_for_role,
        with_actor=with_actor,
    )


def render_ai_assistant(
    current_df: pd.DataFrame,
    comparison_df: pd.DataFrame,
    vuln_df: pd.DataFrame,
    readiness_df: pd.DataFrame,
    qa_df: pd.DataFrame,
    metrics_df: pd.DataFrame,
) -> None:
    render_assistant_chat(
        current_df,
        comparison_df,
        vuln_df,
        readiness_df,
        qa_df,
        metrics_df,
        render_context_selector=render_context_selector,
        run_async=run_async,
    )


def render_operations(config: dict[str, Any]) -> None:
    with st.expander("Release Input Upload", expanded=False):
        render_input_upload(embedded=True)
    app_pages.render_operations(config, page_context())


def render_dashboard(current_df: pd.DataFrame, comparison_df: pd.DataFrame, vuln_df: pd.DataFrame, metrics_df: pd.DataFrame) -> None:
    app_pages.render_dashboard(current_df, comparison_df, vuln_df, metrics_df, page_context())


def render_dashboard_page(current_df: pd.DataFrame, comparison_df: pd.DataFrame, vuln_df: pd.DataFrame, metrics_df: pd.DataFrame) -> None:
    app_pages.render_dashboard_page(current_df, comparison_df, vuln_df, metrics_df, page_context())


def render_context_selector(location: str = "dashboard") -> None:
    app_pages.render_context_selector(page_context(), location)


def render_inventory(current_df: pd.DataFrame) -> None:
    app_pages.render_inventory(current_df, page_context())


def render_latest(latest_df: pd.DataFrame) -> None:
    app_pages.render_latest(latest_df, page_context())


def render_comparison(comparison_df: pd.DataFrame) -> None:
    app_pages.render_comparison(comparison_df, page_context())


def render_package_readiness(readiness_df: pd.DataFrame) -> None:
    app_pages.render_package_readiness(readiness_df, page_context())


def render_compatibility_check(qa_df: pd.DataFrame) -> None:
    app_pages.render_compatibility_check(qa_df, page_context())


def render_qa_validation(qa_df: pd.DataFrame) -> None:
    qa_validation_page.render_qa_validation(qa_df, page_context())


def render_vulnerabilities(vuln_df: pd.DataFrame) -> None:
    security_page.render_vulnerabilities(vuln_df, page_context())


def render_vulnerability_war_room() -> None:
    vulnerability_war_room_page.render_vulnerability_war_room(page_context())


def render_reports(current_df: pd.DataFrame, comparison_df: pd.DataFrame, vuln_df: pd.DataFrame) -> None:
    app_pages.render_reports(current_df, comparison_df, vuln_df, page_context())


def render_cache(cache_metrics: dict[str, Any]) -> None:
    admin_pages.render_cache(cache_metrics, page_context())


def render_audit(metrics_df: pd.DataFrame, cache_metrics: dict[str, Any]) -> None:
    admin_pages.render_audit(metrics_df, cache_metrics, page_context())


def render_settings(config: dict[str, Any]) -> None:
    admin_pages.render_settings(config, page_context())


def render_admin_user_management() -> None:
    admin_pages.render_admin_user_management(page_context())


def render_input_upload(embedded: bool = False) -> None:
    admin_pages.render_input_upload(page_context(), embedded=embedded)


def main() -> None:
    inject_css()
    base_config = load_config()
    if not require_login(base_config, allowed_teams_for_user):
        return
    config = active_config(base_config)
    sync_background_schedule_from_config(config)

    output_files = config.get("output_files", {})
    current_file = project_path(output_files.get("current_version_json", "output/current_versions.json"))
    latest_file = project_path(output_files.get("latest_version_json", "output/latest_versions.json"))
    comparison_file = project_path(output_files.get("comparison_report_json", "output/comparison_report.json"))
    vulnerability_file = project_path(output_files.get("vulnerability_report_json", "output/vulnerability_report.json"))
    cache_metrics_file = project_path(output_files.get("cache_metrics_file", "output/cache/cache_metrics.json"))
    metrics_file = project_path(output_files.get("metrics_file", "output/metrics.jsonl"))

    current = load_json(str(current_file), file_mtime(current_file))
    latest = load_json(str(latest_file), file_mtime(latest_file))
    comparison = load_json(str(comparison_file), file_mtime(comparison_file))
    vulnerabilities = load_json(str(vulnerability_file), file_mtime(vulnerability_file))
    cache_metrics = load_json(str(cache_metrics_file), file_mtime(cache_metrics_file))
    metrics_df = load_metrics(str(metrics_file), file_mtime(metrics_file))

    current_df = normalize_current(current)
    latest_df = normalize_latest(latest)
    comparison_df = normalize_comparison(comparison, vulnerabilities)
    vuln_df = normalize_vulnerabilities(vulnerabilities)
    vendor_requirements = load_vendor_compatibility_requirements(comparison, latest) if comparison and latest else {}
    package_readiness, qa_validation = load_workspace_outputs()
    readiness_df = normalize_package_readiness(package_readiness)
    qa_df = normalize_qa_validation(qa_validation)
    compatibility_df = build_transient_compatibility_df(comparison, latest, vendor_requirements)
    if compatibility_df.empty:
        compatibility_df = qa_df

    workflow_status = "Completed" if not comparison_df.empty else "Not Run"
    last_scan = last_scan_time(latest, vulnerabilities)
    pages = pages_for_role(
        current_role(),
        base_pages=BASE_PAGES,
        release_pages=RELEASE_PAGES,
        qa_pages=QA_PAGES,
        security_pages=SECURITY_PAGES,
        cache_pages=CACHE_PAGES,
        role_assistant_pages=ROLE_ASSISTANT_PAGES,
        admin_pages=ADMIN_PAGES,
        action_roles=ACTION_ROLES,
        workflow_monitor_page=WORKFLOW_MONITOR_PAGE,
    )
    page = render_sidebar(base_config, workflow_status, last_scan, pages=pages, next_scan=next_scan_time(config))
    render_app_header(page, workflow_status, last_scan)

    if page == "Dashboard":
        render_dashboard_page(current_df, comparison_df, vuln_df, metrics_df)
    elif page in set(ROLE_ASSISTANT_PAGES.values()):
        render_ai_assistant(current_df, comparison_df, vuln_df, readiness_df, qa_df, metrics_df)
    elif page == "Operations":
        render_operations(config)
    elif page == "Software Inventory":
        render_inventory(current_df)
    elif page == "Latest Versions":
        render_latest(latest_df)
    elif page == "Version Comparison":
        render_comparison(comparison_df)
    elif page == "Package Readiness":
        render_package_readiness(readiness_df)
    elif page == "Release Decision Gate":
        release_readiness_gate_page.render_release_readiness_gate(page_context())
    elif page == "Compatibility Review":
        render_compatibility_check(compatibility_df)
    elif page == "QA Validation":
        render_qa_validation(qa_df)
    elif page == "Vulnerability Assessment":
        render_vulnerabilities(vuln_df)
    elif page == "Vulnerability Evidence":
        render_vulnerability_war_room()
    elif page == WORKFLOW_MONITOR_PAGE:
        if current_role() != ROLE_ADMIN:
            render_access_denied("Admin")
        else:
            workflow_ui.render_workflow(metrics_df, page_context())
    elif page == "Cache Analytics":
        render_cache(cache_metrics)
    elif page == "Reports":
        render_reports(current_df, comparison_df, vuln_df)
    elif page == "Audit Logs":
        render_audit(metrics_df, cache_metrics)
    elif page == "Admin User Management":
        render_admin_user_management()
    elif page == "Settings":
        render_settings(config)


if __name__ == "__main__":
    main()
