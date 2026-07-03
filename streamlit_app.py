from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from types import SimpleNamespace

import altair as alt
import pandas as pd
import streamlit as st
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from App.auth import (
    ROLE_ADMIN,
    ROLE_QA_ENGINEER,
    ROLE_RELEASE_ENGINEER,
    can_manage_settings,
    can_run_operations,
    clear_user_session,
    configured_users,
    current_role,
    current_user,
    require_login,
    user_team_scope,
)
from App.emailing import prepare_email_report_files, qa_report_attachments
from App import pages as app_pages
from App.qa_history import (
    append_qa_history,
    build_qa_signoff_history_record,
    calculate_qa_summary,
    executed_count,
    history_dataframe,
    load_qa_history,
    load_release_qa_history,
)
from App.qa_updates import QAUpdateConflict, build_qa_update_payload, save_qa_row_update
from App.qa_signoff import build_qa_signoff, load_qa_signoff, save_qa_signoff
from App.scan_reports import (
    load_parsed_scan_findings,
    parse_scan_report,
    save_parsed_scan_findings,
    save_uploaded_scan_report,
)
from App.workspace import (
    BASE_DIR,
    DEFAULT_TEAM_LABEL,
    OUTPUT_DIR,
    RELEASE_OUTPUT_KEYS,
    active_config,
    active_output_path,
    active_release_line,
    active_team_name,
    allowed_teams_for_user,
    config_path_for_result,
    create_team_snapshot,
    list_teams,
    project_path,
    scoped_config_for_context,
    team_workspace_output_dir,
    team_input_software_path,
)
from App.workflow_locks import WorkflowAlreadyRunning, workflow_lock
from App.user_store import DEFAULT_USER_DB, PERMISSION_QA_SIGNOFF, ROLES, delete_user, list_user_audit, list_users, set_user_active, upsert_user
from Core.compatibility_fetcher import CompatibilityRequirementFetcher
from Core.comparator import compare
from Core.excel_reporter import generate_excel_report
from Core.notifier import count_actionable_updates, get_last_email_error, is_actionable_update, send_email
from Core.pdf_reader import PDFReader
from Core.server_querier import ServerQuerier
from Core.testcase_impact import save_testcase_impact_outputs
from Core.version_fetcher import VersionFetcher
from Core.vulnerability_checker import VulnerabilityChecker
from Core.workspace_assessment import (
    build_compatibility_assessment,
    build_package_readiness,
    build_qa_validation,
)
from Utils.software_loader import load_software, load_software_metadata
from Utils.utils import load_config, logger
from Utils.version_format import canonical_version
from agent.memory import get_run_history as read_run_history
from agent.memory import log_audit
from agent.multi_agent import LangGraphVersionManager
from mcp_server import _load_json, _resolve_current_version, _run_pipeline, _save_json, _vulnerability_path


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
    "Latest Versions",
    "Version Comparison",
    "Compatibility Check",
    "Workflow Monitor",
    "Reports",
]
SECURITY_PAGES = ["Vulnerability Assessment", "Cache Analytics"]
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
    page_title="Version Manager",
    page_icon="VM",
    layout="wide",
    initial_sidebar_state="expanded",
)


def inject_css() -> None:
    st.markdown(
        """
        <style>
        :root {
            --vm-bg: #070d14;
            --vm-sidebar: #0a111d;
            --vm-panel: #111a28;
            --vm-panel-2: #0f1724;
            --vm-panel-3: #162235;
            --vm-border: #26364c;
            --vm-border-soft: #1c2a3d;
            --vm-text: #f3f7fb;
            --vm-muted: #a7b7ca;
            --vm-blue: #60a5fa;
            --vm-cyan: #38bdf8;
            --vm-green: #22c55e;
            --vm-yellow: #f59e0b;
            --vm-orange: #fb923c;
            --vm-red: #ef4444;
            --vm-shadow: 0 16px 36px rgba(0,0,0,0.24);
        }
        .stApp {
            background: var(--vm-bg);
            color: var(--vm-text);
        }
        .block-container {
            padding-top: 1.2rem;
            padding-bottom: 2rem;
            max-width: 1420px;
        }
        section[data-testid="stSidebar"] {
            background: var(--vm-sidebar);
            border-right: 1px solid var(--vm-border);
        }
        section[data-testid="stSidebar"] h3 {
            color: var(--vm-text) !important;
            font-size: 1.08rem;
            margin-bottom: 0.25rem;
        }
        section[data-testid="stSidebar"] p,
        section[data-testid="stSidebar"] span,
        section[data-testid="stSidebar"] label {
            color: var(--vm-muted) !important;
        }
        section[data-testid="stSidebar"] .stRadio label {
            color: var(--vm-text) !important;
            font-weight: 650;
        }
        div[data-testid="stCheckbox"] label,
        div[data-testid="stCheckbox"] p,
        div[data-testid="stCheckbox"] span,
        div[data-testid="stToggle"] label,
        div[data-testid="stToggle"] p,
        div[data-testid="stToggle"] span {
            color: var(--vm-text) !important;
            opacity: 1 !important;
            font-weight: 750;
        }
        div[data-testid="stCheckbox"] svg,
        div[data-testid="stToggle"] svg {
            color: #6b7280 !important;
            opacity: 1 !important;
        }
        div[data-testid="stNumberInput"] label,
        div[data-testid="stNumberInput"] p,
        div[data-testid="stNumberInput"] span {
            color: var(--vm-text) !important;
            opacity: 1 !important;
            font-weight: 750;
        }
        div[data-testid="stNumberInput"] input {
            color: #111827 !important;
            background: #ffffff !important;
            font-weight: 650;
        }
        div[data-testid="stNumberInput"] button {
            color: #111827 !important;
            background: #ffffff !important;
        }
        div[data-testid="stSelectbox"] label,
        div[data-testid="stSelectbox"] p,
        div[data-testid="stSelectbox"] span,
        div[data-testid="stTextInput"] label,
        div[data-testid="stTextInput"] p,
        div[data-testid="stTextInput"] span,
        label[data-testid="stWidgetLabel"],
        label[data-testid="stWidgetLabel"] p,
        label[data-testid="stWidgetLabel"] span {
            color: var(--vm-text) !important;
            opacity: 1 !important;
            font-weight: 750;
        }
        div[data-testid="stSelectbox"] div,
        div[data-testid="stTextInput"] input {
            color: #111827;
        }
        div[data-testid="stSidebarUserContent"] {
            padding-top: 0.55rem;
        }
        h1, h2, h3 {
            letter-spacing: 0;
        }
        .stButton button, .stDownloadButton button {
            border-radius: 8px;
            border: 1px solid var(--vm-border);
            background: var(--vm-panel);
            color: var(--vm-text);
            font-weight: 650;
            box-shadow: var(--vm-shadow);
        }
        .stButton button:hover, .stDownloadButton button:hover {
            border-color: #3b82f6;
            color: #ffffff;
            background: #16243a;
        }
        div[data-testid="stMetric"] {
            background: linear-gradient(135deg, var(--vm-panel) 0%, var(--vm-panel-2) 100%);
            border: 1px solid var(--vm-border);
            border-radius: 8px;
            padding: 13px 15px;
            min-height: 92px;
            box-shadow: var(--vm-shadow);
        }
        div[data-testid="stMetric"] label {
            color: var(--vm-muted) !important;
            font-size: 0.78rem;
            font-weight: 800;
            opacity: 1 !important;
        }
        div[data-testid="stMetric"] [data-testid="stMetricValue"] {
            color: var(--vm-text);
            font-size: 1.55rem;
        }
        div[data-testid="stMetricDelta"] {
            font-size: 0.75rem;
        }
        .vm-shell-header {
            display: grid;
            grid-template-columns: 1fr auto;
            gap: 12px;
            align-items: center;
            border: 1px solid var(--vm-border);
            background: linear-gradient(135deg, var(--vm-panel) 0%, var(--vm-panel-2) 100%);
            border-radius: 8px;
            padding: 16px 18px;
            margin-bottom: 18px;
            box-shadow: var(--vm-shadow);
        }
        .vm-shell-header h1 {
            margin: 0;
            font-size: 1.22rem;
            color: var(--vm-text);
        }
        .vm-shell-header p {
            margin: 3px 0 0 0;
            color: var(--vm-muted);
            font-size: 0.78rem;
        }
        .vm-header-actions {
            display: flex;
            gap: 8px;
            justify-content: flex-end;
            flex-wrap: wrap;
        }
        .vm-chip {
            border: 1px solid var(--vm-border);
            background: #162235;
            color: var(--vm-text);
            border-radius: 999px;
            padding: 4px 8px;
            font-size: 0.7rem;
            font-weight: 650;
            white-space: nowrap;
        }
        .vm-title {
            border-bottom: 1px solid var(--vm-border);
            padding-bottom: 10px;
            margin: 8px 0 16px 0;
        }
        .vm-title h1 {
            font-size: 1.7rem;
            margin: 0;
            letter-spacing: 0;
        }
        .vm-title p {
            color: var(--vm-muted);
            margin: 4px 0 0 0;
        }
        .vm-card {
            background: var(--vm-panel);
            border: 1px solid var(--vm-border);
            border-radius: 8px;
            padding: 16px;
            margin-bottom: 16px;
            box-shadow: var(--vm-shadow);
        }
        .vm-posture {
            display: grid;
            grid-template-columns: 1.4fr repeat(3, 1fr);
            gap: 12px;
            margin-bottom: 16px;
        }
        .vm-posture-item {
            border: 1px solid var(--vm-border);
            border-radius: 8px;
            background: var(--vm-panel);
            padding: 15px;
            min-height: 104px;
            box-shadow: var(--vm-shadow);
        }
        .vm-posture-item.primary {
            background: linear-gradient(135deg, #142034 0%, #172b40 55%, #123044 100%);
            border-color: #31516d;
        }
        .vm-posture-label {
            color: var(--vm-muted);
            font-size: 0.78rem;
            font-weight: 700;
            text-transform: uppercase;
        }
        .vm-posture-value {
            color: var(--vm-text);
            font-size: 1.55rem;
            font-weight: 780;
            margin-top: 8px;
        }
        .vm-posture-note {
            color: var(--vm-muted);
            font-size: 0.78rem;
            margin-top: 6px;
        }
        .vm-status {
            display: inline-flex;
            align-items: center;
            border-radius: 999px;
            padding: 3px 9px;
            font-size: 0.75rem;
            font-weight: 700;
            border: 1px solid var(--vm-border);
        }
        .vm-status.ok { color: #bbf7d0; background: rgba(34,197,94,0.16); border-color: rgba(34,197,94,0.34); }
        .vm-status.warn { color: #fde68a; background: rgba(245,158,11,0.16); border-color: rgba(245,158,11,0.34); }
        .vm-status.bad { color: #fecaca; background: rgba(239,68,68,0.16); border-color: rgba(239,68,68,0.34); }
        .vm-status.info { color: #bae6fd; background: rgba(56,189,248,0.16); border-color: rgba(56,189,248,0.34); }
        .vm-status.neutral { color: #cbd5e1; background: rgba(148,163,184,0.14); border-color: rgba(148,163,184,0.3); }
        .vm-grid-2 {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 14px;
        }
        .vm-flow {
            display: grid;
            grid-template-columns: repeat(6, minmax(120px, 1fr));
            gap: 10px;
            align-items: stretch;
        }
        .vm-node {
            border: 1px solid var(--vm-border);
            border-radius: 8px;
            background: var(--vm-panel);
            padding: 14px 12px;
            min-height: 104px;
            position: relative;
            box-shadow: var(--vm-shadow);
        }
        .vm-node:before {
            content: "";
            position: absolute;
            left: 12px;
            top: 0;
            width: 34px;
            height: 3px;
            background: var(--vm-cyan);
            border-radius: 0 0 4px 4px;
        }
        .vm-node strong {
            display: block;
            color: var(--vm-text);
            font-size: 0.86rem;
            margin-bottom: 8px;
        }
        .vm-node span {
            color: var(--vm-muted);
            font-size: 0.76rem;
        }
        .vm-separator {
            height: 1px;
            background: var(--vm-border);
            margin: 18px 0;
        }
        .stDataFrame, div[data-testid="stTable"] {
            border: 1px solid var(--vm-border);
            border-radius: 8px;
        }
        .vm-table-wrap {
            border: 1px solid #d8dee8;
            border-radius: 8px;
            overflow: auto;
            background: #ffffff;
            max-height: 560px;
        }
        table.vm-readable-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.86rem;
            color: #111827;
            background: #ffffff;
        }
        table.vm-readable-table thead th {
            position: sticky;
            top: 0;
            z-index: 1;
            background: #e8eef6;
            color: #111827;
            font-weight: 850;
            text-align: left;
            padding: 9px 10px;
            border-bottom: 1px solid #cbd5e1;
            white-space: nowrap;
        }
        table.vm-readable-table tbody td {
            padding: 8px 10px;
            border-bottom: 1px solid #e5eaf0;
            color: #1f2937;
            font-weight: 520;
            vertical-align: top;
            white-space: nowrap;
        }
        table.vm-readable-table tbody tr:nth-child(even) td {
            background: #fafcff;
        }
        table.vm-readable-table tbody tr:hover td {
            background: #eef6ff;
        }
        table.vm-readable-table td.vm-cell-red {
            background: #fce8e6 !important;
            color: #8a1c12 !important;
            font-weight: 800;
        }
        table.vm-readable-table td.vm-cell-amber {
            background: #fff4d6 !important;
            color: #7a4b00 !important;
            font-weight: 800;
        }
        table.vm-readable-table td.vm-cell-blue {
            background: #e8f2ff !important;
            color: #174a7c !important;
            font-weight: 800;
        }
        table.vm-readable-table td.vm-cell-green {
            background: #e7f6ed !important;
            color: #135d31 !important;
            font-weight: 800;
        }
        [data-testid="stDataFrame"] div[role="grid"] {
            border-color: var(--vm-border) !important;
        }
        [data-testid="stDataFrame"] [role="columnheader"],
        [data-testid="stDataFrame"] [role="columnheader"] div,
        [data-testid="stDataFrame"] [role="columnheader"] span {
            color: #111827 !important;
            background: #eef3f8 !important;
            font-weight: 850 !important;
            opacity: 1 !important;
        }
        [data-testid="stDataFrame"] [role="gridcell"],
        [data-testid="stDataFrame"] [role="gridcell"] div,
        [data-testid="stDataFrame"] [role="gridcell"] span {
            color: #1f2937 !important;
            opacity: 1 !important;
        }
        .vm-sidebar-card {
            border: 1px solid var(--vm-border);
            border-radius: 8px;
            background: var(--vm-panel);
            padding: 12px;
            margin: 10px 0 14px 0;
            box-shadow: var(--vm-shadow);
        }
        .vm-sidebar-kv {
            color: var(--vm-muted);
            font-size: 0.78rem;
            margin-bottom: 8px;
        }
        .vm-sidebar-kv strong {
            display: block;
            color: var(--vm-text);
            font-size: 0.82rem;
            margin-top: 1px;
        }
        div[data-testid="stMarkdownContainer"],
        div[data-testid="stMarkdownContainer"] p,
        div[data-testid="stMarkdownContainer"] li,
        div[data-testid="stMarkdownContainer"] span {
            color: var(--vm-text);
        }
        h1, h2, h3, h4 {
            color: var(--vm-text) !important;
        }
        div[data-testid="stAlert"] {
            border-radius: 8px;
            border: 1px solid var(--vm-border);
        }
        div[data-testid="stExpander"] {
            border: 1px solid var(--vm-border);
            border-radius: 8px;
            background: var(--vm-panel);
            box-shadow: var(--vm-shadow);
        }
        div[data-baseweb="select"] > div,
        div[data-testid="stTextInput"] input,
        div[data-testid="stNumberInput"] input {
            background: #ffffff !important;
            border-color: #d8dee8 !important;
            color: #202123 !important;
            border-radius: 8px !important;
        }
        div[data-baseweb="select"] span {
            color: #202123 !important;
        }
        div[data-testid="stTabs"] button {
            color: var(--vm-muted) !important;
            border-radius: 999px;
        }
        div[data-testid="stTabs"] button[aria-selected="true"] {
            color: var(--vm-text) !important;
            background: #162235 !important;
            font-weight: 750;
        }
        section[data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] label {
            border-radius: 8px;
            padding: 4px 6px;
        }
        section[data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] label:hover {
            background: #111a28;
        }
        [data-testid="stHeader"] {
            background: rgba(7,13,20,0.92);
        }
        iframe {
            border: 1px solid var(--vm-border) !important;
            border-radius: 8px;
            background: white;
        }
        .vm-login-wrap {
            width: 100%;
            margin: 8vh 0 0 0;
            border: 1px solid var(--vm-border);
            background: linear-gradient(135deg, var(--vm-panel) 0%, var(--vm-panel-2) 100%);
            border-radius: 12px;
            padding: 28px;
            box-shadow: var(--vm-shadow);
        }
        .vm-login-brand {
            font-size: 1.5rem;
            font-weight: 800;
            color: var(--vm-text);
            margin-bottom: 6px;
        }
        .vm-login-subtitle {
            color: var(--vm-muted);
            font-size: 0.88rem;
            line-height: 1.45;
            margin-bottom: 20px;
        }
        .vm-login-meta {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
            margin-bottom: 0;
        }
        div[data-testid="stForm"] {
            border: 0;
            padding: 0;
        }
        div[data-testid="stForm"] button[kind="primary"],
        div[data-testid="stFormSubmitButton"] button {
            background: #2563eb !important;
            border: 1px solid #60a5fa !important;
            color: #ffffff !important;
            font-weight: 800 !important;
            min-height: 42px;
            box-shadow: 0 10px 24px rgba(37,99,235,0.28);
        }
        div[data-testid="stForm"] button[kind="primary"]:hover,
        div[data-testid="stFormSubmitButton"] button:hover {
            background: #1d4ed8 !important;
            color: #ffffff !important;
            border-color: #93c5fd !important;
        }
        @media (max-width: 1100px) {
            .vm-flow { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
            .vm-posture { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            .vm-shell-header { grid-template-columns: 1fr; }
        }
        @media (max-width: 720px) {
            .vm-posture { grid-template-columns: 1fr; }
            .vm-grid-2 { grid-template-columns: 1fr; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def file_mtime(path: Path) -> float:
    return path.stat().st_mtime if path.exists() else 0.0


@st.cache_data
def load_json(path: str, mtime: float = 0.0) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        return {}
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


@st.cache_data
def load_metrics(path: str, mtime: float = 0.0) -> pd.DataFrame:
    file_path = Path(path)
    rows: list[dict[str, Any]] = []
    if not file_path.exists():
        return pd.DataFrame()
    for line in file_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return pd.DataFrame(rows)


@st.cache_data
def load_file_text(path: str, mtime: float = 0.0) -> str:
    file_path = Path(path)
    if not file_path.exists():
        return ""
    return file_path.read_text(encoding="utf-8", errors="ignore")


def clear_dashboard_cache() -> None:
    load_json.clear()
    load_metrics.clear()
    load_file_text.clear()


def save_qa_manual_update(
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
    clear_dashboard_cache()
    return updated


def run_async(coro: Any) -> Any:
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def runtime_state(team: str | None = None, release: str | None = None) -> dict[str, Any]:
    if team is not None and release is not None:
        config = scoped_config_for_context(load_config(), team, release)
    else:
        config = active_config(load_config())
    return {
        "config": config,
        "scoped_config": team is not None and release is not None,
        "version_fetcher": VersionFetcher(),
        "pdf_reader": PDFReader(config),
        "server_querier": ServerQuerier(),
        "vulnerability_checker": VulnerabilityChecker(),
    }


def app_state_db_path(team: str | None = None, release: str | None = None) -> Path:
    if team is not None and release is not None:
        return team_workspace_output_dir(team, release) / "app_state.db"
    return active_output_path("__placeholder__").parent / "app_state.db"


def workflow_owner() -> str:
    user = current_user()
    return str(user.get("username") or user.get("display_name") or "unknown")


def active_workflow_context(team: str | None = None, release: str | None = None) -> dict[str, str]:
    team = team or active_team_name()
    release = release or active_release_line(team)
    output_dir = team_workspace_output_dir(team, release)
    return {
        "active_team": team,
        "active_release": release,
        "active_output_dir": str(output_dir),
    }


def build_streamlit_agent_tools(state: dict[str, Any]) -> dict[str, Any]:
    config = state["config"]

    async def get_software_list(category: str = "ALL") -> dict[str, Any]:
        software = load_software(config["input_files"]["software_yml"], category)
        return {"category": category, "software": software}

    async def query_server(software_name: str) -> dict[str, Any]:
        result = await state["server_querier"].fetch(software_name)
        if result:
            result.setdefault("source", "live server")
        return result or {"source": "live server", "error": "No version returned"}

    async def extract_from_pdf(software_name: str) -> dict[str, Any]:
        result = await state["pdf_reader"].fetch(software_name)
        result.setdefault("source", "PDF fallback")
        return result

    async def search_latest_version(software_name: str, force_refresh: bool = False) -> dict[str, Any]:
        return await state["version_fetcher"].fetch(software_name, force_refresh=force_refresh)

    async def compare_versions(latest: dict | None = None, current: dict | None = None) -> dict[str, Any]:
        latest = latest or {}
        current = current or {}
        comparison = compare(latest, current)
        _save_json(latest, config["output_files"]["latest_version_json"])
        _save_json(current, config["output_files"]["current_version_json"])
        _save_json(comparison, config["output_files"]["comparison_report_json"])
        return comparison

    async def get_run_history(software_name: str, limit: int = 5) -> dict[str, Any]:
        return {"software_name": software_name, "history": read_run_history(software_name, limit)}

    async def check_vulnerabilities(
        software_name: str,
        version: str | None = None,
        needs_update: bool = False,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        return await state["vulnerability_checker"].check(
            software_name,
            version,
            needs_update,
            force_refresh=force_refresh,
        )

    async def save_vulnerability_report(vulnerabilities: dict) -> dict[str, Any]:
        out = _vulnerability_path(config)
        _save_json(vulnerabilities, out)
        return {"saved": True, "path": str((BASE_DIR / out).resolve()), "total": len(vulnerabilities)}

    async def assess_package_readiness(
        comparison: dict | None = None,
        latest: dict | None = None,
        vulnerabilities: dict | None = None,
    ) -> dict[str, Any]:
        readiness = build_package_readiness(comparison or {}, latest or {}, vulnerabilities or {})
        out = config["output_files"].get("package_readiness_json", "output/package_readiness.json")
        _save_json(readiness, out)
        return {
            "saved": True,
            "path": str((BASE_DIR / out).resolve()),
            "total": len(readiness),
            "package_readiness": readiness,
        }

    async def save_package_readiness(package_readiness: dict) -> dict[str, Any]:
        out = config["output_files"].get("package_readiness_json", "output/package_readiness.json")
        _save_json(package_readiness, out)
        return {"saved": True, "path": str((BASE_DIR / out).resolve()), "total": len(package_readiness)}

    async def check_compatibility(
        comparison: dict | None = None,
        package_readiness: dict | None = None,
    ) -> dict[str, Any]:
        metadata = load_software_metadata(config["input_files"]["software_yml"], config.get("default_category", "ALL"))
        comparison = comparison or {}
        latest = _load_json(config["output_files"].get("latest_version_json", "output/latest_versions.json"))
        vendor_requirements = await resolve_vendor_compatibility_requirements(comparison, latest)
        compatibility = build_compatibility_assessment(comparison, package_readiness or {}, metadata, vendor_requirements)
        return {"saved": True, "total": len(compatibility), "compatibility": compatibility}

    async def generate_qa_validation(
        comparison: dict | None = None,
        package_readiness: dict | None = None,
    ) -> dict[str, Any]:
        metadata = load_software_metadata(config["input_files"]["software_yml"], config.get("default_category", "ALL"))
        comparison = comparison or {}
        latest = _load_json(config["output_files"].get("latest_version_json", "output/latest_versions.json"))
        vendor_requirements = await resolve_vendor_compatibility_requirements(comparison, latest)
        qa_validation = build_qa_validation(comparison, package_readiness or {}, metadata, vendor_requirements)
        out = config["output_files"].get("qa_validation_json", "output/qa_validation.json")
        _save_json(qa_validation, out)
        return {
            "saved": True,
            "path": str((BASE_DIR / out).resolve()),
            "total": len(qa_validation),
            "qa_validation": qa_validation,
        }

    async def save_qa_validation(qa_validation: dict) -> dict[str, Any]:
        out = config["output_files"].get("qa_validation_json", "output/qa_validation.json")
        _save_json(qa_validation, out)
        return {"saved": True, "path": str((BASE_DIR / out).resolve()), "total": len(qa_validation)}

    async def generate_testcase_impact(comparison: dict | None = None) -> dict[str, Any]:
        comparison = comparison or _load_json(config["output_files"]["comparison_report_json"])
        impact = save_testcase_impact_outputs(
            comparison,
            str(project_path(config["input_files"].get("testcase_repository_xlsx", "Input/testcaseRepository.xlsx"))),
            str(project_path(config["output_files"].get("testcase_impact_json", "output/testcase_impact.json"))),
            str(project_path(config["output_files"].get("testcase_impact_xlsx", "output/Test_Case_Impact_Assessment.xlsx"))),
        )
        return {
            "saved": True,
            "path": str(project_path(config["output_files"].get("testcase_impact_json", "output/testcase_impact.json"))),
            "excel_path": str(project_path(config["output_files"].get("testcase_impact_xlsx", "output/Test_Case_Impact_Assessment.xlsx"))),
            "summary": impact.get("summary", {}),
            "testcase_impact": impact,
        }

    async def generate_excel_assessment() -> dict[str, Any]:
        comparison = _load_json(config["output_files"]["comparison_report_json"])
        vulnerability_path = _vulnerability_path(config)
        vulnerabilities = _load_json(vulnerability_path) if Path(BASE_DIR / vulnerability_path).exists() else {}
        excel_path = BASE_DIR / config["output_files"].get("excel_assessment", "output/Software_Version_Assessment.xlsx")
        generate_excel_report(comparison, vulnerabilities, str(excel_path))
        return {"saved": True, "path": str(excel_path)}

    async def send_notification(report: dict | None = None) -> dict[str, Any]:
        comparison = _load_json(config["output_files"]["comparison_report_json"])
        vulnerability_path = _vulnerability_path(config)
        vulnerabilities = _load_json(vulnerability_path) if Path(BASE_DIR / vulnerability_path).exists() else {}
        body, html_body = prepare_email_report_files(comparison, vulnerabilities)
        actionable_updates = count_actionable_updates(comparison, vulnerabilities)
        unknown = [name for name, result in comparison.items() if result.get("unknown")]
        subject = (
            f"{actionable_updates} software update(s) needed"
            if actionable_updates else (
                f"{len(unknown)} software version status unknown"
                if unknown else "All software versions are up to date"
            )
        )
        attachments = qa_report_attachments(config)
        sent = send_email(subject, body, html_body=html_body, attachments=attachments)
        return {
            "sent": sent,
            "subject": subject,
            "attachments": [path.name for path in attachments],
            "error": get_last_email_error(),
        }

    async def log_audit_event(step: str, details: dict | None = None) -> dict[str, Any]:
        run_id = (details or {}).get("run_id", "streamlit")
        log_audit(run_id, step, "streamlit", details or {})
        return {"logged": True, "run_id": run_id, "step": step}

    return {
        "get_software_list": get_software_list,
        "query_server": query_server,
        "extract_from_pdf": extract_from_pdf,
        "search_latest_version": search_latest_version,
        "compare_versions": compare_versions,
        "get_run_history": get_run_history,
        "check_vulnerabilities": check_vulnerabilities,
        "save_vulnerability_report": save_vulnerability_report,
        "assess_package_readiness": assess_package_readiness,
        "save_package_readiness": save_package_readiness,
        "check_compatibility": check_compatibility,
        "generate_qa_validation": generate_qa_validation,
        "save_qa_validation": save_qa_validation,
        "generate_testcase_impact": generate_testcase_impact,
        "generate_excel_assessment": generate_excel_assessment,
        "send_notification": send_notification,
        "log_audit_event": log_audit_event,
    }


async def trigger_full_pipeline(
    category: str,
    force_refresh: bool,
    team: str | None = None,
    release: str | None = None,
) -> dict[str, Any]:
    workflow_team = team or active_team_name()
    workflow_release = release or active_release_line(workflow_team)
    try:
        with workflow_lock(
            app_state_db_path(workflow_team, workflow_release),
            team=workflow_team,
            release=workflow_release,
            scope="workflow",
            owner=workflow_owner(),
        ):
            state = runtime_state(workflow_team, workflow_release)
            workflow = LangGraphVersionManager(build_streamlit_agent_tools(state))
            final_state = await workflow.run(
                "Run the full software version, security, package readiness, compatibility, QA validation, and reporting workflow.",
                category=category,
                force_refresh=force_refresh,
            )
    except WorkflowAlreadyRunning as exc:
        return {"error": str(exc), "operation": "full_pipeline"}
    comparison = final_state.get("comparison_results", {})
    vulnerabilities = final_state.get("vulnerability_results", {})
    report_package = final_state.get("report_package", {})
    notification = report_package.get("notification", {})
    excel = report_package.get("excel", {})
    updates = [name for name, result in comparison.items() if is_actionable_update(result)]
    return {
        "operation": "full_pipeline",
        "workflow": "LangGraph Supervisor",
        **active_workflow_context(workflow_team, workflow_release),
        "workflow_status": final_state.get("workflow_status"),
        "agent_messages": final_state.get("messages", []),
        "cache_mode": "fresh" if force_refresh else "use_cache",
        "total": len(comparison),
        "needs_update": updates,
        "unknown": [name for name, result in comparison.items() if result.get("unknown")],
        "vulnerability_report": config_path_for_result("vulnerability_report_json"),
        "package_readiness_report": config_path_for_result("package_readiness_json"),
        "qa_validation_report": config_path_for_result("qa_validation_json"),
        "testcase_impact_report": config_path_for_result("testcase_impact_json"),
        "testcase_impact_excel": config_path_for_result("testcase_impact_xlsx"),
        "excel_assessment": excel.get("path", config_path_for_result("excel_assessment")),
        "vulnerabilities": vulnerabilities,
        "package_readiness": final_state.get("package_readiness_results", {}),
        "compatibility": final_state.get("compatibility_results", {}),
        "qa_validation": final_state.get("qa_validation_results", {}),
        "testcase_impact": final_state.get("testcase_impact_results", {}),
        "email_sent": bool(notification.get("sent")),
        "email_error": notification.get("error"),
    }


async def trigger_scoped_pipeline(
    category: str,
    force_refresh: bool,
    workflow_scope: str,
    team: str | None = None,
    release: str | None = None,
) -> dict[str, Any]:
    workflow_team = team or active_team_name()
    workflow_release = release or active_release_line(workflow_team)
    try:
        with workflow_lock(
            app_state_db_path(workflow_team, workflow_release),
            team=workflow_team,
            release=workflow_release,
            scope="workflow",
            owner=workflow_owner(),
        ):
            state = runtime_state(workflow_team, workflow_release)
            summary = await _run_pipeline(state, category, force_refresh=force_refresh, workflow_scope=workflow_scope)
    except WorkflowAlreadyRunning as exc:
        return {"error": str(exc), "operation": f"{workflow_scope}_workflow"}
    if "error" in summary:
        return {"error": summary["error"], "operation": f"{workflow_scope}_workflow"}
    return {
        "operation": f"{workflow_scope}_workflow",
        "workflow": "Scoped Backend Pipeline",
        **active_workflow_context(workflow_team, workflow_release),
        "workflow_scope": summary.get("workflow_scope"),
        "cache_mode": summary.get("cache_mode"),
        "total": summary.get("total", 0),
        "needs_update": summary.get("needs_update", []),
        "unknown": summary.get("unknown", []),
        "vulnerability_report": summary.get("vulnerability_report"),
        "package_readiness_report": summary.get("package_readiness_report"),
        "qa_validation_report": summary.get("qa_validation_report"),
        "testcase_impact_report": summary.get("testcase_impact_report"),
        "testcase_impact_excel": summary.get("testcase_impact_excel"),
        "excel_assessment": summary.get("excel_assessment"),
        "vulnerabilities": summary.get("vulnerabilities", {}),
        "package_readiness": summary.get("package_readiness", {}),
        "qa_validation": summary.get("qa_validation", {}),
        "testcase_impact": summary.get("testcase_impact", {}),
        "email_sent": summary.get("email_sent"),
        "email_error": summary.get("email_error"),
    }


async def trigger_package_workflow(
    category: str,
    force_refresh: bool,
    team: str | None = None,
    release: str | None = None,
) -> dict[str, Any]:
    return await trigger_scoped_pipeline(category, force_refresh, "package", team, release)


async def trigger_qa_workflow(
    category: str,
    force_refresh: bool,
    team: str | None = None,
    release: str | None = None,
) -> dict[str, Any]:
    return await trigger_scoped_pipeline(category, force_refresh, "qa", team, release)


async def trigger_shared_scan(
    category: str,
    force_refresh: bool,
    team: str | None = None,
    release: str | None = None,
) -> dict[str, Any]:
    return await trigger_scoped_pipeline(category, force_refresh, "shared", team, release)


async def trigger_fetch_latest_versions(category: str, force_refresh: bool) -> dict[str, Any]:
    state = runtime_state()
    config = state["config"]
    software_list = load_software(config["input_files"]["software_yml"], category)
    latest = {}
    for name in software_list:
        latest[name] = await state["version_fetcher"].fetch(name, force_refresh=force_refresh)
    out = config["output_files"]["latest_version_json"]
    _save_json(latest, out)
    return {
        "operation": "fetch_latest_versions",
        "saved": True,
        "total": len(latest),
        "cache_mode": "fresh" if force_refresh else "use_cache",
        "path": str((BASE_DIR / out).resolve()),
    }


async def trigger_fetch_current_versions(category: str) -> dict[str, Any]:
    state = runtime_state()
    config = state["config"]
    software_list = load_software(config["input_files"]["software_yml"], category)
    current = {}
    scanned_at = datetime.now().astimezone().isoformat()
    for name in software_list:
        record = await _resolve_current_version(state["server_querier"], state["pdf_reader"], name)
        if isinstance(record, dict):
            record["last_scanned"] = scanned_at
        current[name] = record
    out = config["output_files"]["current_version_json"]
    _save_json(current, out)
    from_server = len([item for item in current.values() if item.get("source") == "live server"])
    return {
        "operation": "fetch_current_versions",
        "saved": True,
        "total": len(current),
        "from_server": from_server,
        "from_document": len(current) - from_server,
        "path": str((BASE_DIR / out).resolve()),
    }


def trigger_compare_versions() -> dict[str, Any]:
    config = active_config(load_config())
    latest = _load_json(config["output_files"]["latest_version_json"])
    current = _load_json(config["output_files"]["current_version_json"])
    comparison = compare(latest, current)
    out = config["output_files"]["comparison_report_json"]
    _save_json(comparison, out)
    return {
        "operation": "compare_versions",
        "saved": True,
        "total": len(comparison),
        "needs_update": len([item for item in comparison.values() if item.get("needs_update")]),
        "path": str((BASE_DIR / out).resolve()),
    }


def trigger_send_report_email() -> dict[str, Any]:
    config = active_config(load_config())
    comparison = _load_json(config["output_files"]["comparison_report_json"])
    vulnerability_path = _vulnerability_path(config)
    vulnerabilities = _load_json(vulnerability_path) if Path(BASE_DIR / vulnerability_path).exists() else {}
    body, html_body = prepare_email_report_files(comparison, vulnerabilities)
    needs_update = [name for name, result in comparison.items() if result.get("needs_update")]
    actionable_updates = count_actionable_updates(comparison, vulnerabilities)
    unknown = [name for name, result in comparison.items() if result.get("unknown")]
    subject = (
        f"{actionable_updates} software update(s) needed"
        if actionable_updates else (
            f"{len(unknown)} software version status unknown"
            if unknown else "All software versions are up to date"
        )
    )
    attachments = qa_report_attachments(config)
    sent = send_email(subject, body, html_body=html_body, attachments=attachments)
    return {
        "operation": "send_report_email",
        "sent": sent,
        "subject": subject,
        "attachments": [path.name for path in attachments],
        "recipients": len(config.get("smtp", {}).get("recipients", [])),
        "error": get_last_email_error(),
    }


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
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Config file not found: {CONFIG_FILE}")
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    config["schedule_cron"] = schedule
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


def value(record: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return default


def display_version(software_name: str, record: dict[str, Any], *keys: str) -> str:
    return canonical_version(software_name, value(record, *keys))


def safe_int(raw: Any, default: int = 0) -> int:
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return default


def parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def format_ts(raw: str | None) -> str:
    parsed = parse_ts(raw)
    if not parsed:
        return "Not available"
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def format_epoch_ts(raw: float) -> str:
    if not raw:
        return "Not available"
    return datetime.fromtimestamp(raw).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def inventory_status(record: dict[str, Any]) -> str:
    build_version = str(value(record, "Build Version", "version", default="")).strip()
    source = str(value(record, "source", default="")).lower()
    if not build_version or build_version.lower() in {"unknown", "none", "null"}:
        return "Unknown"
    if "unreachable" in source:
        return "Discovered via Document"
    return "Discovered"


def vendor_for(name: str) -> str:
    lookup = {
        "sql": "Microsoft",
        "exchange": "Microsoft",
        "outlook": "Microsoft",
        "edge": "Microsoft",
        "openssl": "OpenSSL",
        "libcurl": "curl",
        "hcl": "HCL",
        "elastic": "Elastic",
    }
    lowered = name.lower()
    for key, vendor in lookup.items():
        if key in lowered:
            return vendor
    return "Unknown"


def version_gap(current_version: str, latest_version: str, current_cu: str = "", latest_cu: str = "") -> str:
    current_version = str(current_version or "").strip()
    latest_version = str(latest_version or "").strip()
    current_cu = str(current_cu or "").strip()
    latest_cu = str(latest_cu or "").strip()
    if not current_version or not latest_version:
        return "Unknown"
    if current_version.lower() == latest_version.lower() and current_cu.lower() == latest_cu.lower():
        return "None"
    if current_version.lower() == latest_version.lower() and current_cu and latest_cu and current_cu.lower() != latest_cu.lower():
        return "CU Gap"
    if mixed_version_scheme(current_version, latest_version):
        return "Source Review"
    current_major = current_version.split(".")[0]
    latest_major = latest_version.split(".")[0]
    if current_major and latest_major and current_major != latest_major:
        return "Major Gap"
    return "Patch Gap"


def update_priority(gap: str, risk: str) -> str:
    risk = risk.upper()
    if risk in {"CRITICAL", "HIGH"}:
        return risk.title()
    if gap in {"Major Gap", "CU Gap", "Source Review"}:
        return "Medium"
    if gap in {"Patch Gap", "Minor Gap"}:
        return "Low"
    return "None"


def mixed_version_scheme(current_version: str, latest_version: str) -> bool:
    current_parts = [int(part) for part in re.findall(r"\d+", str(current_version or ""))[:4]]
    latest_parts = [int(part) for part in re.findall(r"\d+", str(latest_version or ""))[:4]]
    if not current_parts or not latest_parts:
        return False
    if current_parts[0] == latest_parts[0]:
        return False
    return max(current_parts[0], latest_parts[0]) >= 100 or abs(current_parts[0] - latest_parts[0]) >= 50


def load_workspace_outputs() -> tuple[dict[str, Any], dict[str, Any]]:
    config = active_config(load_config())
    package_path = project_path(config["output_files"].get("package_readiness_json", "output/package_readiness.json"))
    qa_path = project_path(config["output_files"].get("qa_validation_json", "output/qa_validation.json"))
    return load_json(str(package_path), file_mtime(package_path)), load_json(str(qa_path), file_mtime(qa_path))


def build_transient_compatibility_df(
    comparison: dict[str, Any],
    latest: dict[str, Any],
    vendor_requirements: dict[str, dict[str, Any]] | None = None,
) -> pd.DataFrame:
    if not comparison:
        return pd.DataFrame()
    config = active_config(load_config())
    compatibility = build_qa_validation(
        comparison,
        {},
        load_software_metadata(config["input_files"]["software_yml"], "ALL"),
        vendor_requirements,
    )
    return normalize_qa_validation(compatibility)


async def resolve_vendor_compatibility_requirements(
    comparison: dict[str, Any],
    latest: dict[str, Any],
    force_refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    fetcher = CompatibilityRequirementFetcher()
    requirements: dict[str, dict[str, Any]] = {}
    for name, record in comparison.items():
        target = record.get("latest", {}) if isinstance(record, dict) else {}
        latest_version = value(target, "Build Version", "version")
        latest_record = latest.get(name, {}) if isinstance(latest, dict) else {}
        metadata = latest_record.get("cache_metadata", {}) if isinstance(latest_record, dict) else {}
        source_url = value(latest_record, "Release Notes", "source_url", default="")
        if not source_url:
            source_url = value(metadata, "source", default="")
        extracted = await fetcher.fetch(name, str(latest_version or ""), str(source_url or ""), force_refresh=force_refresh)
        if extracted:
            requirements[name] = extracted
    return requirements


def load_vendor_compatibility_requirements(
    comparison: dict[str, Any],
    latest: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    try:
        return run_async(resolve_vendor_compatibility_requirements(comparison, latest, force_refresh=False))
    except Exception as exc:
        logger.warning("Vendor compatibility extraction unavailable: %s", exc)
        return {}


def normalize_current(data: dict[str, Any]) -> pd.DataFrame:
    rows = []
    fallback_scan_time = format_epoch_ts(file_mtime(active_output_path("current_versions.json")))
    for name, record in data.items():
        rows.append(
            {
                "Software Name": name,
                "Vendor": vendor_for(name),
                "Current Version": display_version(name, record, "Build Version", "version"),
                "Current CU": value(record, "Cumulative Update (CU)", "cu", default=""),
                "Server Name": "Configured Server" if value(record, "source") == "live server" else "PDF Inventory",
                "Environment": "Production",
                "Last Scanned": format_ts(value(record, "last_scanned", default="")) if value(record, "last_scanned", default="") else fallback_scan_time,
                "Source": value(record, "source", default="Unknown"),
            }
        )
    return pd.DataFrame(rows)


def configured_inventory_from_active_software_yml() -> tuple[pd.DataFrame, Path]:
    input_path = team_input_software_path(active_team_name(), active_release_line())
    metadata = load_software_metadata(str(input_path), "ALL") if input_path.exists() else {}
    rows = []
    for name, details in metadata.items():
        requirements = details.get("current_requirements", {}) if isinstance(details, dict) else {}
        requirements = requirements if isinstance(requirements, dict) else {}
        rows.append(
            {
                "Software Name": name,
                "Vendor": vendor_for(name),
                "Current Version": "Pending scan",
                "Current CU": "",
                "Server Name": "Pending scan",
                "Environment": "Production",
                "Last Scanned": "Not scanned",
                "Source": "software.yml",
                "OS Requirement": requirements.get("os", ""),
                "Database Requirement": requirements.get("database_version", ""),
                "Architecture": requirements.get("architecture", ""),
            }
        )
    return pd.DataFrame(rows), input_path


def normalize_latest(data: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for name, record in data.items():
        metadata = record.get("cache_metadata", {})
        rows.append(
            {
                "Software Name": name,
                "Vendor": vendor_for(name),
                "Latest Version": display_version(name, record, "Build Version", "version"),
                "Latest CU": value(record, "Cumulative Update (CU)", "cu", default=""),
                "Last Checked": format_ts(metadata.get("last_updated")),
                "Source": metadata.get("source", value(record, "source", default="vendor_sources")),
                "Cache Status": str(metadata.get("status", "unknown")).title(),
            }
        )
    return pd.DataFrame(rows)


def normalize_comparison(data: dict[str, Any], vulnerabilities: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for name, record in data.items():
        current = record.get("current", {})
        latest = record.get("latest", {})
        current_version = display_version(name, current, "Build Version", "version")
        latest_version = display_version(name, latest, "Build Version", "version")
        current_cu = value(current, "Cumulative Update (CU)", "cu", default="")
        latest_cu = value(latest, "Cumulative Update (CU)", "cu", default="")
        gap = version_gap(str(current_version), str(latest_version), str(current_cu), str(latest_cu))
        needs_update = is_actionable_update(record)
        risk = value(vulnerabilities.get(name, {}), "risk_level", default="UNKNOWN").upper()
        rows.append(
            {
                "Software Name": name,
                "Current Version": current_version,
                "Latest Version": latest_version,
                "Current CU": current_cu,
                "Latest CU": latest_cu,
                "Version Gap": gap,
                "Need Update": "Yes" if needs_update else "No",
                "Update Priority": update_priority(gap, risk),
                "Status": "Source Review" if gap == "Source Review" else ("Outdated" if needs_update else "Up-to-date"),
                "Risk Level": risk,
            }
        )
    return pd.DataFrame(rows)


def normalize_vulnerabilities(data: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for name, record in data.items():
        cves = record.get("cves") or []
        rows.append(
            {
                "Software Name": value(record, "software_name", default=name),
                "Current Installed Version": canonical_version(name, value(record, "current_version", "version")),
                "Latest Available Version": canonical_version(name, value(record, "latest_version", default="")),
                "Version Assessed": value(record, "version_assessed", default="current"),
                "CVE Severity": value(record, "severity", default="UNKNOWN").upper(),
                "Risk Level": value(record, "risk_level", default="UNKNOWN").upper(),
                "CVE Count": len(cves),
                "Security Assessment": value(record, "assessment", default="No assessment available."),
                "Source": vulnerability_source_label(value(record, "source", default="Unknown")),
            }
        )
    return pd.DataFrame(rows)


def vulnerability_source_label(source: str) -> str:
    source_value = str(source or "Unknown").lower()
    labels = []
    if "nvd" in source_value and "error" not in source_value:
        labels.append("NVD")
    if "local-assessment" in source_value or "error" in source_value:
        labels.append("NVD unavailable fallback")
    if "policy" in source_value:
        labels.append("Policy baseline")
    return " + ".join(labels) if labels else str(source or "Unknown")


def normalize_package_readiness(data: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for name, record in data.items():
        software_name = value(record, "Software Name", default=name)
        rows.append(
            {
                "Software Name": software_name,
                "Vendor": value(record, "Vendor", default=vendor_for(name)),
                "Current Version": canonical_version(software_name, value(record, "Current Version")),
                "Target Version": canonical_version(software_name, value(record, "Target Version")),
                "Package Readiness": value(record, "Package Readiness", default="Not Assessed"),
                "Upgrade Impact": value(record, "Upgrade Impact", default="Medium"),
                "Installer Type": value(record, "Installer Type", default="Vendor Package"),
                "Owner": value(record, "Owner", default="Application Owner"),
                "Release Notes": value(record, "Release Notes", default="Review vendor release notes."),
                "Download Link": value(record, "Download Link", default="Vendor portal"),
                "Blocker": value(record, "Blocker", default=""),
            }
        )
    return pd.DataFrame(rows)


def normalize_qa_validation(data: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for name, record in data.items():
        test_case_count = safe_int(value(record, "Test Case Count", default=0))
        test_cases_passed = safe_int(value(record, "Test Cases Passed", default=0))
        test_cases_failed = safe_int(value(record, "Test Cases Failed", default=0))
        test_cases_blocked = safe_int(value(record, "Test Cases Blocked / Not Tested", default=0))
        calculated_executed = test_cases_passed + test_cases_failed + test_cases_blocked
        raw_executed = safe_int(value(record, "Test Cases Executed", default=0))
        test_cases_executed = calculated_executed or raw_executed
        test_cases_executed = min(test_cases_executed, test_case_count) if test_case_count else test_cases_executed
        test_case_coverage = (
            f"{((test_cases_executed / test_case_count) * 100):.1f}%".replace(".0%", "%")
            if test_case_count
            else "Not Required"
        )
        rows.append(
            {
                "Software Name": value(record, "Software Name", default=name),
                "Current Version": canonical_version(name, value(record, "Current Version")),
                "Latest Version": canonical_version(name, value(record, "Target Version", "Package Version")),
                "Package Version": canonical_version(name, value(record, "Package Version")),
                "Installation Status": value(record, "Installation Status", default="Not Tested"),
                "Compatibility Status": value(record, "Compatibility Status", default="Review Required"),
                "Test Result": value(record, "Test Result", default="NOT TESTED"),
                "Supported OS": value(record, "Supported OS", "Windows Version", default="Not available"),
                "Supported Runtime": value(record, "Supported Runtime", ".NET Version", "Java Version", default="Not available"),
                "Supported Browser": value(record, "Supported Browser", "Browser Version", default="Not available"),
                "Database Dependency": value(record, "Database Dependency", "Supported Database", "Database Version", default="Not available"),
                "Supported Architecture": value(record, "Supported Architecture", "OS Architecture", default="Not available"),
                "Configured Environment": value(record, "Current Environment", default="Not provided in software.yml"),
                "Current Environment": value(record, "Current Environment", default="Not provided in software.yml"),
                "Requirement Source": value(record, "Requirement Source", default="Built-in compatibility rule"),
                "Requirement Source URL": value(record, "Requirement Source URL", default=""),
                "Requirement Confidence": value(record, "Requirement Confidence", default="Not available"),
                "Last Verified": value(record, "Last Verified", default="Not available"),
                "Test Case Count": test_case_count,
                "Test Cases Passed": test_cases_passed,
                "Test Cases Failed": test_cases_failed,
                "Test Cases Blocked / Not Tested": test_cases_blocked,
                "Test Cases Executed": test_cases_executed,
                "Test Case Coverage %": test_case_coverage,
                "Test Notes": value(record, "Test Notes"),
                "Test Date": value(record, "Test Date", default=""),
                "Tested By": value(record, "Tested By", default=""),
                "Evidence File": value(record, "Evidence File", default=""),
                "QA Revision": safe_int(value(record, "QA Revision", default=0)),
                "Last QA Update": value(record, "Last QA Update", default=""),
                "Last QA Updated By": value(record, "Last QA Updated By", default=""),
            }
        )
    return pd.DataFrame(rows)


def normalize_testcase_impact(data: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for item in data.get("test_case_plan", []) or []:
        software_name = value(item, "Software Name")
        rows.append({
            "Software Name": software_name,
            "Current Version": canonical_version(software_name, value(item, "Current Version")),
            "Target Version": canonical_version(software_name, value(item, "Target Version")),
            "Test Case Source": value(item, "Test Case Source", "Test Coverage", default="Not Found"),
            "Test Case ID": value(item, "Test Case ID"),
            "Test Case Name": value(item, "Test Case Name"),
            "Test Type": value(item, "Test Type"),
            "Priority": value(item, "Priority", default=value(item, "Recommended Priority", default="High")),
            "Automation Status": value(item, "Automation Status"),
            "Owner": value(item, "Owner", default="QA Lead"),
            "Applicable Version": value(item, "Applicable Version"),
            "Precondition": value(item, "Precondition"),
            "Expected Result": value(item, "Expected Result"),
            "Recommendation": value(item, "Recommendation"),
        })
    return pd.DataFrame(rows)


def add_environment_readiness(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "Compatibility Status" not in df.columns:
        return df
    copy = df.copy()
    copy["Environment Readiness"] = copy["Compatibility Status"].replace({
        "Review Required": "Needs Environment Review",
        "Compatible": "Compatible / No Review Needed",
    })
    return copy


def compliance_score(comparison_df: pd.DataFrame) -> int:
    if comparison_df.empty:
        return 0
    compliant = (comparison_df["Need Update"] == "No").sum()
    return round((compliant / len(comparison_df)) * 100)


def last_scan_time(*datasets: dict[str, Any]) -> str:
    timestamps: list[datetime] = []
    for dataset in datasets:
        for record in dataset.values():
            if isinstance(record, dict):
                metadata = record.get("cache_metadata") or {}
                parsed = parse_ts(metadata.get("last_updated"))
                if parsed:
                    timestamps.append(parsed)
    if not timestamps and METRICS_FILE.exists():
        metrics = load_metrics(str(METRICS_FILE), file_mtime(METRICS_FILE))
        if not metrics.empty and "ts" in metrics:
            parsed_values = [parse_ts(str(ts)) for ts in metrics["ts"].dropna().tolist()]
            timestamps.extend([item for item in parsed_values if item])
    if not timestamps:
        return "Not available"
    return max(timestamps).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def next_scan_time(config: dict[str, Any]) -> str:
    schedule = config.get("schedule_cron", "")
    if schedule:
        return describe_cron(schedule)
    return (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")


def describe_cron(schedule: str) -> str:
    parts = schedule.split()
    if len(parts) != 5:
        return schedule or "Not configured"
    minute, hour, day_of_month, month, day_of_week = parts
    if minute.startswith("*/") and hour == "*" and day_of_month == "*" and month == "*" and day_of_week == "*":
        return f"Every {minute[2:]} minutes"
    if minute == "0" and hour.startswith("*/") and day_of_month == "*" and month == "*" and day_of_week == "*":
        return f"Every {hour[2:]} hours"
    day_names = {
        "0": "Sunday",
        "1": "Monday",
        "2": "Tuesday",
        "3": "Wednesday",
        "4": "Thursday",
        "5": "Friday",
        "6": "Saturday",
        "7": "Sunday",
    }
    if not minute.isdigit() or not hour.isdigit():
        return f"Custom schedule: {schedule}"
    time_text = f"{hour.zfill(2)}:{minute.zfill(2)}"
    if day_of_month == "*" and month == "*" and day_of_week == "*":
        return f"Daily at {time_text}"
    if day_of_month == "*" and month == "*" and day_of_week in day_names:
        return f"Every {day_names[day_of_week]} at {time_text}"
    if day_of_month != "*" and month == "*" and day_of_week == "*":
        return f"Monthly on day {day_of_month} at {time_text}"
    return f"Custom schedule: {schedule}"


def render_app_header(page: str, workflow_status: str, last_scan: str) -> None:
    status_tone = "ok" if workflow_status == "Completed" else "warn"
    compact_last_scan = last_scan if last_scan == "Not available" else last_scan[5:]
    user = current_user()
    role_label = current_role()
    st.markdown(
        f"""
        <div class="vm-shell-header">
            <div>
                <h1>Version Manager Command Center</h1>
                <p>{page} · Software version, vulnerability, and reporting operations</p>
            </div>
            <div class="vm-header-actions">
                {badge(workflow_status, status_tone)}
                <span class="vm-chip">Last Scan: {compact_last_scan}</span>
                <span class="vm-chip">{user.get("display_name", user.get("username", "User"))} - {role_label}</span>
                <span class="vm-chip">Production</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def section_title(title: str, subtitle: str = "") -> None:
    subtitle_html = f"<p>{subtitle}</p>" if subtitle else ""
    st.markdown(
        f"""
        <div class="vm-title">
            <h1>{title}</h1>
            {subtitle_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def badge(label: str, tone: str = "info") -> str:
    return f'<span class="vm-status {tone}">{label}</span>'


def risk_tone(risk: str) -> str:
    risk = risk.upper()
    if risk in {"CRITICAL", "HIGH"}:
        return "bad"
    if risk == "MEDIUM":
        return "warn"
    if risk in {"LOW", "NONE"}:
        return "ok"
    return "info"


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


def style_operational_table(df: pd.DataFrame) -> Any:
    if df.empty:
        return df

    def color_cell(value: Any) -> str:
        text = str(value).upper()
        if text in {"CRITICAL", "HIGH", "YES", "OUTDATED"}:
            return "background-color: #FCE8E6; color: #8A1C12; font-weight: 700; border: 1px solid #F4B8B2;"
        if text in {"MEDIUM", "MAJOR GAP", "CU GAP"}:
            return "background-color: #FFF4D6; color: #7A4B00; font-weight: 700; border: 1px solid #F3D58A;"
        if text in {"LOW", "MINOR GAP"}:
            return "background-color: #E8F2FF; color: #174A7C; font-weight: 700; border: 1px solid #BBD7F2;"
        if text in {"NO", "UP-TO-DATE", "NONE", "ACTIVE"}:
            return "background-color: #E7F6ED; color: #135D31; font-weight: 700; border: 1px solid #B9E3C8;"
        if text in {"INACTIVE - LOGIN DISABLED"}:
            return "background-color: #F3F4F6; color: #4B5563; font-weight: 700; border: 1px solid #D1D5DB;"
        return ""

    return (
        df.style
        .map(color_cell)
        .set_properties(**{"color": "#1F2937", "background-color": "#FFFFFF"})
        .set_table_styles(
            [
                {
                    "selector": "th",
                    "props": [
                        ("background-color", "#F3F6FA"),
                        ("color", "#1F2937"),
                        ("font-weight", "700"),
                        ("border-bottom", "1px solid #D8DEE8"),
                    ],
                },
                {
                    "selector": "td",
                    "props": [
                        ("border-bottom", "1px solid #E5EAF0"),
                    ],
                },
            ]
        )
    )


def readable_cell_class(value: Any) -> str:
    text = str(value).upper()
    if text in {"CRITICAL", "HIGH", "YES", "OUTDATED"}:
        return "vm-cell-red"
    if text in {"MEDIUM", "MAJOR GAP", "CU GAP"}:
        return "vm-cell-amber"
    if text in {"LOW", "MINOR GAP"}:
        return "vm-cell-blue"
    if text in {"NO", "UP-TO-DATE", "NONE"}:
        return "vm-cell-green"
    return ""


def render_access_denied(required: str) -> None:
    st.warning(f"This page requires {required} access. Your current role is {current_role()}.")


def pages_for_role(role: str) -> list[str]:
    pages = [*BASE_PAGES]
    if role in {ROLE_ADMIN, ROLE_RELEASE_ENGINEER}:
        insert_at = pages.index("Compatibility Check") if "Compatibility Check" in pages else len(pages)
        for page in reversed(RELEASE_PAGES):
            pages.insert(insert_at, page)
    if role in {ROLE_ADMIN, ROLE_RELEASE_ENGINEER}:
        insert_at = pages.index("Workflow Monitor") if "Workflow Monitor" in pages else len(pages)
        for page in reversed(SECURITY_PAGES):
            pages.insert(insert_at, page)
    elif role == ROLE_QA_ENGINEER:
        insert_at = pages.index("Workflow Monitor") if "Workflow Monitor" in pages else len(pages)
        pages.insert(insert_at, "Cache Analytics")
    if role in {ROLE_ADMIN, ROLE_QA_ENGINEER}:
        insert_at = pages.index("Workflow Monitor") if "Workflow Monitor" in pages else len(pages)
        for page in reversed(QA_PAGES):
            pages.insert(insert_at, page)
    if role in ADMIN_ROLES:
        pages.extend(ADMIN_PAGES)
    return pages


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


def with_actor(result: dict[str, Any]) -> dict[str, Any]:
    user = current_user()
    result["triggered_by"] = user.get("username", "unknown")
    result["triggered_by_role"] = current_role()
    result["triggered_at"] = datetime.now().isoformat(timespec="seconds")
    return result


def can_perform_qa_signoff() -> bool:
    return current_role() == ROLE_ADMIN or PERMISSION_QA_SIGNOFF in current_user().get("permissions", [])


def render_readable_table(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("No records found.")
        return
    header_html = "".join(f"<th>{col}</th>" for col in df.columns)
    body_rows = []
    safe_df = df.fillna("").astype(str)
    for _, row in safe_df.iterrows():
        cells = []
        for value in row:
            css_class = readable_cell_class(value)
            class_attr = f' class="{css_class}"' if css_class else ""
            escaped = (
                value.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
            )
            cells.append(f"<td{class_attr}>{escaped}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    st.markdown(
        f"""
        <div class="vm-table-wrap">
            <table class="vm-readable-table">
                <thead><tr>{header_html}</tr></thead>
                <tbody>{''.join(body_rows)}</tbody>
            </table>
        </div>
        """,
        unsafe_allow_html=True,
    )


def bar_chart(df: pd.DataFrame, x: str, y: str, title: str, color_field: str | None = None) -> None:
    if df.empty:
        st.info(f"No data available for {title}.")
        return
    height = max(240, min(420, 78 + (len(df) * 46)))
    chart = (
        alt.Chart(df)
        .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
        .encode(
            y=alt.Y(
                f"{x}:N",
                title=None,
                sort="-x",
                axis=alt.Axis(labelLimit=260, labelPadding=8),
            ),
            x=alt.X(
                f"{y}:Q",
                title=None,
                axis=alt.Axis(grid=True, tickMinStep=1),
            ),
            tooltip=list(df.columns),
        )
        .properties(height=height, title=title)
        .configure_view(stroke=None)
        .configure_axis(
            labelColor="#1f2937",
            titleColor="#1f2937",
            labelFontSize=12,
            titleFontSize=12,
        )
        .configure_title(
            color="#111827",
            fontSize=14,
            anchor="start",
            dy=-4,
        )
    )
    if color_field:
        chart = chart.encode(
            color=alt.Color(
                f"{color_field}:N",
                legend=alt.Legend(orient="bottom", labelLimit=220),
                scale=alt.Scale(range=["#38bdf8", "#22c55e", "#f59e0b", "#f97316", "#ef4444"]),
            )
        )
    st.altair_chart(chart, use_container_width=True)


def donut_chart(df: pd.DataFrame, category: str, value_col: str, title: str) -> None:
    if df.empty:
        st.info(f"No data available for {title}.")
        return
    chart = (
        alt.Chart(df)
        .mark_arc(innerRadius=65, outerRadius=110)
        .encode(
            theta=alt.Theta(f"{value_col}:Q"),
            color=alt.Color(
                f"{category}:N",
                legend=alt.Legend(orient="bottom", labelLimit=220),
                scale=alt.Scale(range=["#22c55e", "#f59e0b", "#f97316", "#ef4444", "#38bdf8", "#94a3b8"]),
            ),
            tooltip=[category, value_col],
        )
        .properties(height=280, title=title)
        .configure_view(stroke=None)
        .configure_axis(labelColor="#1f2937", titleColor="#1f2937")
        .configure_title(color="#111827", fontSize=14, anchor="start", dy=-4)
    )
    st.altair_chart(chart, use_container_width=True)


def searchable_table(df: pd.DataFrame, key: str, filter_columns: list[str] | None = None) -> pd.DataFrame:
    if df.empty:
        st.info("No records found. Run the pipeline to generate the required output files.")
        return df
    query = st.text_input("Search", key=f"{key}_search", placeholder="Search software, vendor, version, status")
    filtered = df.copy()
    if query:
        mask = filtered.astype(str).apply(lambda row: row.str.contains(query, case=False, na=False).any(), axis=1)
        filtered = filtered[mask]
    if filter_columns:
        cols = st.columns(len(filter_columns))
        for col, field in zip(cols, filter_columns):
            values = ["All"] + sorted([str(item) for item in filtered[field].dropna().unique().tolist()])
            selected = col.selectbox(field, values, key=f"{key}_{field}")
            if selected != "All":
                filtered = filtered[filtered[field].astype(str) == selected]
    render_readable_table(filtered)
    st.download_button(
        "Export CSV",
        filtered.to_csv(index=False).encode("utf-8"),
        file_name=f"{key}.csv",
        mime="text/csv",
        use_container_width=False,
    )
    return filtered


def render_sidebar(config: dict[str, Any], workflow_status: str, last_scan: str) -> str:
    with st.sidebar:
        user = current_user()
        active_team = active_team_name()
        active_release = active_release_line(active_team)
        st.markdown("### Version Manager")
        st.caption("Software posture and remediation operations")
        st.markdown(
            f"""
            <div class="vm-sidebar-card">
                <div class="vm-sidebar-kv">Signed In<strong>{user.get("display_name", user.get("username", "Unknown"))}</strong></div>
                <div class="vm-sidebar-kv">Role<strong>{current_role()}</strong></div>
                <div class="vm-sidebar-kv">Project<strong>Version Manager</strong></div>
                <div class="vm-sidebar-kv">Team<strong>{active_team}</strong></div>
                <div class="vm-sidebar-kv">Release Line<strong>{active_release}</strong></div>
                <div class="vm-sidebar-kv">Scope<strong>Version and Security Assessment</strong></div>
                <div class="vm-sidebar-kv">Workflow<strong>{workflow_status}</strong></div>
                <div class="vm-sidebar-kv">Last Scan<strong>{last_scan}</strong></div>
                <div class="vm-sidebar-kv">Next Scan<strong>{next_scan_time(config)}</strong></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("Sign Out", use_container_width=True):
            clear_user_session()
            st.rerun()
        st.divider()
        pages = pages_for_role(current_role())
        if can_run_operations():
            pages.insert(2, "Operations")
        return st.radio(
            "Navigation",
            pages,
            label_visibility="collapsed",
        )


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


def page_context() -> SimpleNamespace:
    return SimpleNamespace(
        apply_background_schedule=apply_background_schedule,
        bar_chart=bar_chart,
        clear_dashboard_cache=clear_dashboard_cache,
        describe_cron=describe_cron,
        file_mtime=file_mtime,
        format_epoch_ts=format_epoch_ts,
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
    section_title("Software Inventory", "Installed software inventory from live servers and document extraction.")
    if current_df.empty:
        configured_df, input_path = configured_inventory_from_active_software_yml()
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
        searchable_table(configured_df.drop(columns=["Source"], errors="ignore"), "software_inventory_configured", ["Vendor", "Environment"])
        return
    display_df = current_df.drop(columns=["Source"], errors="ignore")
    searchable_table(display_df, "software_inventory", ["Vendor", "Environment"])


def render_latest(latest_df: pd.DataFrame) -> None:
    section_title("Latest Versions", "Approved latest-version catalog with source and cache provenance.")
    if latest_df.empty:
        st.info("No latest version records found.")
        return
    display = latest_df.drop(columns=["Cache Status"], errors="ignore")
    searchable_table(display, "latest_versions", ["Vendor", "Source"])


def render_comparison(comparison_df: pd.DataFrame) -> None:
    section_title("Version Comparison", "Current versus latest version analysis and update prioritization.")
    if comparison_df.empty:
        st.info("No comparison data found.")
        return
    score = compliance_score(comparison_df)
    col1, col2, col3 = st.columns(3)
    col1.metric("Compliance Percentage", f"{score}%")
    col2.metric("Outdated Applications", int((comparison_df["Need Update"] == "Yes").sum()) if not comparison_df.empty else 0)
    col3.metric("Critical or Major Gaps", int(comparison_df["Version Gap"].isin(["Major Gap", "CU Gap"]).sum()) if not comparison_df.empty else 0)

    searchable_table(comparison_df, "version_comparison", ["Need Update", "Version Gap", "Update Priority"])

    st.subheader("Version Drift Analysis")
    if not comparison_df.empty:
        drift = comparison_df["Version Gap"].value_counts().reset_index()
        drift.columns = ["Version Gap", "Count"]
        bar_chart(drift, "Version Gap", "Count", "Version Drift by Gap Type")


def render_package_readiness(readiness_df: pd.DataFrame) -> None:
    if current_role() not in {ROLE_ADMIN, ROLE_RELEASE_ENGINEER}:
        render_access_denied("Administrator or Release Engineer")
        return
    section_title("Package Readiness", "Package preparation, vendor review, dependency validation, and upgrade impact.")
    if readiness_df.empty:
        st.info("No package readiness data found. Run version comparison first.")
        return
    counts = readiness_df["Package Readiness"].value_counts().to_dict()
    cols = st.columns(4)
    cols[0].metric("Ready", counts.get("Ready for Packaging", 0))
    cols[1].metric("Vendor Patch Available", counts.get("Vendor Patch Available", 0))
    cols[2].metric("Dependency Review", counts.get("Dependency Review Required", 0))
    cols[3].metric("Blocked", counts.get("Blocked", 0))
    searchable_table(
        readiness_df,
        "package_readiness",
        ["Package Readiness", "Upgrade Impact", "Owner", "Installer Type", "Vendor"],
    )


def render_compatibility_check(qa_df: pd.DataFrame) -> None:
    section_title("Compatibility Check", "Operating system, runtime, browser, database, and architecture readiness for deployment validation.")
    if qa_df.empty:
        st.info("No compatibility data found. Run version comparison first.")
        return
    qa_df = add_environment_readiness(qa_df)
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
    searchable_table(qa_df[columns], "compatibility_check", ["Environment Readiness", "Supported Architecture", "Requirement Confidence"])


def render_qa_validation(qa_df: pd.DataFrame) -> None:
    if current_role() not in {ROLE_ADMIN, ROLE_QA_ENGINEER}:
        render_access_denied("Administrator or QA Engineer")
        return
    section_title("QA Validation Dashboard", "")
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

    searchable_table(qa_df[columns], "qa_validation", ["Deployment Status", "Overall QA Result", "Environment Readiness"])

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
        searchable_table(testcase_df[display_cols], "testcase_impact", ["Test Case Source", "Priority", "Test Type", "Owner"])
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


def render_vulnerabilities(vuln_df: pd.DataFrame) -> None:
    section_title("Vulnerability Assessment", "Security assessment of current installed versions with latest version context.")
    output_dir = active_output_path("__placeholder__").parent
    parsed_scan_findings = load_parsed_scan_findings(output_dir)
    st.subheader("Vulnerability Data Source")
    source_cols = st.columns(3)
    source_cols[0].metric("NVD Lookup", "Available" if not vuln_df.empty else "Not Available")
    source_cols[1].metric("Uploaded Scan Findings", len(parsed_scan_findings))
    source_cols[2].metric("Active Display", "NVD Report" if not vuln_df.empty else "Uploaded Scan Report" if parsed_scan_findings else "No Data")
    with st.expander("Upload Scanner Report", expanded=False):
        st.caption("Supported first-pass formats: JSON, CSV, XLSX, XLS. If no scanner report is available, the page continues to use NVD lookup results.")
        scan_file = st.file_uploader("Upload Vulnerability Scan Report", type=["json", "csv", "xlsx", "xls"], key="vulnerability_scan_report_upload")
        if st.button("Parse Scan Report", use_container_width=True, disabled=scan_file is None):
            try:
                saved_path = save_uploaded_scan_report(output_dir, scan_file)
                findings = parse_scan_report(saved_path)
                save_parsed_scan_findings(output_dir, findings)
                clear_dashboard_cache()
                st.success(f"Parsed {len(findings)} scanner finding(s) from {saved_path.name}.")
                st.rerun()
            except Exception as exc:
                st.error(f"Scan report was not parsed: {exc}")

    if parsed_scan_findings:
        st.subheader("Uploaded Scanner Findings")
        scan_df = pd.DataFrame(parsed_scan_findings)
        scan_cols = [col for col in ["Software Name", "Version", "CVE", "Severity", "Risk Level", "Scanner Source", "Source File", "Parsed At"] if col in scan_df.columns]
        st.dataframe(style_operational_table(scan_df[scan_cols]), use_container_width=True, hide_index=True)

    if vuln_df.empty:
        st.info("No NVD vulnerability data found. Upload a scanner report or run the vulnerability workflow.")
        return
    risk_counts = vuln_df["Risk Level"].value_counts().to_dict()
    cols = st.columns(5)
    for col, risk in zip(cols, ["CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE"]):
        col.metric(f"{risk.title()} Risk", risk_counts.get(risk, 0))

    assessment_cols = [
        "Software Name",
        "Current Installed Version",
        "Latest Available Version",
        "Version Assessed",
        "CVE Severity",
        "Risk Level",
        "Security Assessment",
        "Source",
    ]
    searchable_table(vuln_df[assessment_cols], "vulnerability_assessment", ["Risk Level", "CVE Severity", "Version Assessed", "Source"])

    left, right = st.columns(2)
    with left:
        heatmap_df = vuln_df[["Software Name", "Risk Level", "CVE Count"]].copy()
        heatmap_df["Risk Score"] = heatmap_df["Risk Level"].map({"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}).fillna(0)
        chart = (
            alt.Chart(heatmap_df)
            .mark_rect()
            .encode(
                x=alt.X("Software Name:N", title=None),
                y=alt.Y("Risk Level:N", title=None, sort=RISK_ORDER),
                color=alt.Color("Risk Score:Q", scale=alt.Scale(range=["#1e293b", "#22c55e", "#f59e0b", "#f97316", "#ef4444"])),
                tooltip=list(heatmap_df.columns),
            )
            .properties(height=280, title="Risk Heatmap")
        )
        st.altair_chart(chart, use_container_width=True)
    with right:
        severity_df = vuln_df["CVE Severity"].value_counts().reset_index()
        severity_df.columns = ["Severity", "Count"]
        donut_chart(severity_df, "Severity", "Count", "Severity Distribution")

    st.subheader("Security Review Queue")
    top = vuln_df.sort_values(["CVE Count", "Risk Level"], ascending=[False, True]).head(10)
    st.dataframe(style_operational_table(top[["Software Name", "Risk Level", "CVE Severity", "CVE Count", "Security Assessment"]]), use_container_width=True, hide_index=True)

    posture_score = max(0, 100 - (risk_counts.get("CRITICAL", 0) * 30) - (risk_counts.get("HIGH", 0) * 20) - (risk_counts.get("MEDIUM", 0) * 10))
    st.progress(posture_score / 100, text=f"Security Posture Gauge: {posture_score}%")


def performance_event(metric: str) -> tuple[str, str, str]:
    mapping = {
        "fetch_latest_versions.duration_ms": (
            "Latest Version Check",
            "Checked vendor/latest-version data for the selected software list.",
            "Version Catalog",
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


def build_performance_metrics(metrics_df: pd.DataFrame) -> pd.DataFrame:
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
                "Duration": format_duration_ms(item.get("value")),
                "Items Processed": str(labels.get("total", "Not applicable")),
                "Completed At": format_ts(str(item.get("ts", ""))),
                "Purpose": purpose,
                "Trace ID": item.get("trace_id", ""),
                "Technical Metric": item.get("metric", ""),
            }
        )
    return pd.DataFrame(rows)


def render_workflow(metrics_df: pd.DataFrame) -> None:
    section_title("Workflow Monitor", "Pipeline execution stages and processing duration.")
    nodes = [
        ("Supervisor Agent", "Routes request"),
        ("Discovery Agent", "Inventory collection"),
        ("Research Agent", "Latest versions"),
        ("Analysis Agent", "Version comparison"),
        ("Security Agent", "CVE assessment"),
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
        {"Agent": "Discovery Agent", "Status": "Completed", "Output Summary": "Current software inventory loaded."},
        {"Agent": "Research Agent", "Status": "Completed", "Output Summary": "Latest vendor release catalog generated."},
        {"Agent": "Analysis Agent", "Status": "Completed", "Output Summary": "Version compliance and update status calculated."},
        {"Agent": "Security Agent", "Status": "Completed", "Output Summary": "NVD vulnerability assessment completed."},
        {"Agent": "Reporting Agent", "Status": "Completed", "Output Summary": "Excel, email preview, and notifications prepared."},
    ]
    st.dataframe(style_operational_table(pd.DataFrame(agent_rows)), use_container_width=True, hide_index=True)

    st.subheader("Pipeline Performance")
    performance_df = build_performance_metrics(metrics_df)
    if performance_df.empty:
        st.info("No workflow performance data found.")
    else:
        latest_trace = ""
        if "Trace ID" in performance_df and not performance_df["Trace ID"].dropna().empty:
            latest_trace = str(performance_df["Trace ID"].dropna().iloc[-1])
        latest_run = performance_df[performance_df["Trace ID"] == latest_trace] if latest_trace else performance_df.tail(6)
        display_cols = ["Stage", "Category", "Status", "Duration", "Items Processed", "Completed At", "Purpose"]
        st.dataframe(style_operational_table(latest_run[display_cols]), use_container_width=True, hide_index=True)
        with st.expander("Technical performance details"):
            technical_cols = ["Completed At", "Technical Metric", "Trace ID", "Status", "Duration"]
            st.dataframe(performance_df[technical_cols].tail(20), use_container_width=True, hide_index=True)


def render_cache(cache_metrics: dict[str, Any]) -> None:
    section_title("Cache Analytics", "Operational cache utilization, API reduction, and token savings.")
    namespaces = {key: val for key, val in cache_metrics.items() if isinstance(val, dict)}
    rows = []
    for namespace, record in namespaces.items():
        hits = int(record.get("hits", 0))
        misses = int(record.get("misses", 0))
        total = hits + misses
        rows.append(
            {
                "Namespace": namespace,
                "Cache Area": CACHE_NAMESPACE_LABELS.get(namespace, namespace.replace("_", " ").title()),
                "Cache Hits": hits,
                "Cache Misses": misses,
                "Hit Ratio": round((hits / total) * 100, 1) if total else 0,
                "API Calls Saved": int(record.get("estimated_api_calls_saved", 0)),
                "Estimated Token Savings": int(record.get("estimated_tokens_saved", 0)),
                "Bypasses": int(record.get("bypasses", 0)),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        st.info("No cache metrics available.")
        return
    primary_df = df[df["Namespace"].isin(PRIMARY_CACHE_NAMESPACES)].copy()
    advanced_df = df[~df["Namespace"].isin(PRIMARY_CACHE_NAMESPACES)].copy()
    if primary_df.empty:
        primary_df = df.copy()

    totals = primary_df[["Cache Hits", "Cache Misses", "API Calls Saved", "Estimated Token Savings"]].sum()
    cols = st.columns(5)
    cols[0].metric("Cache Hits", int(totals["Cache Hits"]))
    cols[1].metric("Cache Misses", int(totals["Cache Misses"]))
    total_requests = totals["Cache Hits"] + totals["Cache Misses"]
    cols[2].metric("Hit Ratio", f"{round((totals['Cache Hits'] / total_requests) * 100, 1) if total_requests else 0}%")
    cols[3].metric("API Calls Saved", int(totals["API Calls Saved"]))
    cols[4].metric("Token Savings", int(totals["Estimated Token Savings"]))

    display_cols = ["Cache Area", "Cache Hits", "Cache Misses", "Hit Ratio", "API Calls Saved", "Estimated Token Savings", "Bypasses"]
    st.dataframe(primary_df[display_cols], use_container_width=True, hide_index=True)
    left, right = st.columns(2)
    with left:
        bar_chart(primary_df, "Cache Area", "Hit Ratio", "Cache Efficiency", "Cache Area")
    with right:
        bar_chart(primary_df, "Cache Area", "API Calls Saved", "API Calls Avoided", "Cache Area")

    if not advanced_df.empty:
        with st.expander("Advanced cache details"):
            st.caption("Internal cache layers used for troubleshooting vendor search, LLM parsing, and direct source fetches.")
            st.dataframe(advanced_df[display_cols], use_container_width=True, hide_index=True)


def render_reports(current_df: pd.DataFrame, comparison_df: pd.DataFrame, vuln_df: pd.DataFrame) -> None:
    app_pages.render_reports(current_df, comparison_df, vuln_df, page_context())


def friendly_event(metric: str) -> tuple[str, str, str]:
    mapping = {
        "fetch_latest_versions.duration_ms": (
            "Latest versions refreshed",
            "The system checked the latest approved vendor versions.",
            "Version Catalog",
        ),
        "fetch_current_versions.duration_ms": (
            "Current inventory refreshed",
            "The system collected installed versions from servers or fallback documents.",
            "Inventory",
        ),
        "compare_versions.duration_ms": (
            "Version comparison completed",
            "Installed versions were compared against the latest-version catalog.",
            "Compliance",
        ),
        "check_vulnerabilities.duration_ms": (
            "Vulnerability assessment completed",
            "Current installed versions were checked against vulnerability data.",
            "Security",
        ),
        "send_notification.duration_ms": (
            "Email notification processed",
            "The version assessment report email was generated and sent using configured mail settings.",
            "Notification",
        ),
    }
    return mapping.get(metric, ("Workflow event recorded", "A system workflow event was recorded.", "System"))


def format_duration_ms(raw: Any) -> str:
    try:
        value_ms = float(raw)
    except (TypeError, ValueError):
        return "Not available"
    if value_ms < 1000:
        return f"{value_ms:.0f} ms"
    return f"{value_ms / 1000:.1f} sec"


def build_audit_events(metrics_df: pd.DataFrame, cache_metrics: dict[str, Any]) -> pd.DataFrame:
    rows = []
    if not metrics_df.empty:
        for _, item in metrics_df.tail(100).iterrows():
            labels = item.get("labels", {})
            if not isinstance(labels, dict):
                labels = {}
            title, description, category = friendly_event(str(item.get("metric", "")))
            rows.append(
                {
                    "Timestamp": format_ts(str(item.get("ts", ""))),
                    "Category": category,
                    "Activity": title,
                    "Status": str(labels.get("status", "ok")).title(),
                    "Duration": format_duration_ms(item.get("value")),
                    "Details": description,
                    "Trace ID": item.get("trace_id", ""),
                    "Technical Event": item.get("metric", ""),
                }
            )

    cache_updated = format_ts(cache_metrics.get("last_updated")) if cache_metrics else "Not available"
    for namespace, record in cache_metrics.items():
        if isinstance(record, dict):
            hits = int(record.get("hits", 0))
            misses = int(record.get("misses", 0))
            saved = int(record.get("estimated_api_calls_saved", 0))
            rows.append(
                {
                    "Timestamp": cache_updated,
                    "Category": "Cache",
                    "Activity": f"{namespace.replace('_', ' ').title()} cache updated",
                    "Status": "Ok",
                    "Duration": "Not applicable",
                    "Details": f"{hits} cache hits, {misses} misses, {saved} API calls saved.",
                    "Trace ID": "",
                    "Technical Event": namespace,
                }
            )
    return pd.DataFrame(rows)


def render_audit(metrics_df: pd.DataFrame, cache_metrics: dict[str, Any]) -> None:
    section_title("Activity History", "Readable operational history for scans, reports, cache usage, and notifications.")
    df = build_audit_events(metrics_df, cache_metrics)
    if df.empty:
        st.info("No activity history is available yet. Run the pipeline to create workflow events.")
        return

    total_events = len(df)
    successful = int((df["Status"].str.upper() == "OK").sum())
    cache_events = int((df["Category"] == "Cache").sum())
    last_event = df["Timestamp"].iloc[-1] if not df.empty else "Not available"
    cols = st.columns(4)
    cols[0].metric("Activities Recorded", total_events)
    cols[1].metric("Successful Events", successful)
    cols[2].metric("Cache Events", cache_events)
    cols[3].metric("Latest Activity", last_event)

    display_cols = ["Timestamp", "Category", "Activity", "Status", "Duration", "Details"]
    searchable_table(df[display_cols], "activity_history", ["Category", "Status"])

    with st.expander("Technical audit details"):
        technical_cols = ["Timestamp", "Technical Event", "Trace ID", "Status", "Duration"]
        st.dataframe(df[technical_cols], use_container_width=True, hide_index=True)


def render_settings(config: dict[str, Any]) -> None:
    if not can_manage_settings():
        render_access_denied("Admin")
        return

    section_title("Settings", "Runtime controls and integration status.")
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
    st.dataframe(style_operational_table(pd.DataFrame(status_rows)), use_container_width=True, hide_index=True)

def render_admin_user_management() -> None:
    if not can_manage_settings():
        render_access_denied("Admin")
        return

    section_title("Admin User Management", "Create users, assign roles, control team scope, and audit account changes.")
    action_status = st.session_state.get("admin_user_action_status")
    st.subheader("Access Control")
    st.caption("Release Engineer prepares assessments and reports. QA Engineer validates deployments. Admin manages users, teams, and settings.")
    current_username = current_user().get("username", "admin")
    user_rows = [
        {
            "Delete?": False,
            "User": user["username"],
            "Display Name": user.get("display_name", user["username"]),
            "Role": user["role"],
            "Team Scope": ", ".join(user.get("team_scope", ["*"])),
            "QA Signoff": "Yes" if PERMISSION_QA_SIGNOFF in user.get("permissions", []) else "No",
            "Account Status": "Active" if user.get("active", True) else "Inactive - login disabled",
            "Last Login": user.get("last_login_at") or "Never",
            "Can Run Scans": "Yes" if user["role"] in ACTION_ROLES else "No",
            "Can Manage Settings": "Yes" if user["role"] in ADMIN_ROLES else "No",
        }
        for user in list_users(DEFAULT_USER_DB, include_inactive=True)
    ]
    user_table = pd.DataFrame(user_rows)
    edited_user_table = st.data_editor(
        user_table,
        use_container_width=True,
        hide_index=True,
        disabled=[column for column in user_table.columns if column != "Delete?"],
        column_config={
            "Delete?": st.column_config.CheckboxColumn(
                "Delete?",
                help="Select user account(s) created by mistake, then click Delete Checked User(s).",
                default=False,
            )
        },
        key="admin_user_delete_table",
    )
    delete_candidates = edited_user_table[edited_user_table["Delete?"]]["User"].tolist() if not edited_user_table.empty else []
    delete_disabled = not delete_candidates
    if st.button("Delete Checked User(s)", disabled=delete_disabled, use_container_width=True):
        if current_username in delete_candidates:
            st.session_state["admin_user_action_status"] = "Current logged-in user cannot be deleted."
            st.rerun()
        st.session_state["admin_user_pending_delete"] = delete_candidates
        st.rerun()
    pending_delete = st.session_state.get("admin_user_pending_delete", [])
    if pending_delete:
        st.warning(
            "You are about to permanently delete user account(s): "
            f"{', '.join(pending_delete)}. Audit history will be retained."
        )
        confirm_cols = st.columns(2)
        if confirm_cols[0].button("Confirm Delete", use_container_width=True):
            deleted_users = []
            for target_username in pending_delete:
                if delete_user(target_username, current_username, DEFAULT_USER_DB):
                    deleted_users.append(target_username)
            if deleted_users:
                st.session_state["admin_user_action_status"] = f"Deleted user(s): {', '.join(deleted_users)}."
                st.session_state["admin_user_reset_after_save"] = True
            else:
                st.session_state["admin_user_action_status"] = "No selected users were deleted."
            st.session_state.pop("admin_user_pending_delete", None)
            st.rerun()
        if confirm_cols[1].button("Cancel Delete", use_container_width=True):
            st.session_state["admin_user_action_status"] = "Delete cancelled."
            st.session_state.pop("admin_user_pending_delete", None)
            st.rerun()
    st.caption("Inactive users are retained here for audit history, but they cannot sign in.")

    st.subheader("Create or Update User")
    st.caption("Create users, assign roles, limit team scope, and deactivate access without editing config.json.")
    if st.session_state.pop("admin_user_reset_after_save", False):
        st.session_state["admin_user_action"] = "Create new user"
        st.session_state["admin_user_form_reset_token"] = st.session_state.get("admin_user_form_reset_token", 0) + 1
    form_reset_token = st.session_state.setdefault("admin_user_form_reset_token", 0)
    existing_users = list_users(DEFAULT_USER_DB, include_inactive=True)
    usernames = ["Create new user"] + [user["username"] for user in existing_users]
    selected_username = st.selectbox("User Action", usernames, key="admin_user_action")
    selected_user = next((user for user in existing_users if user["username"] == selected_username), None)
    form_key_suffix = selected_user["username"] if selected_user else f"new_{form_reset_token}"

    with st.form("admin_user_management_form"):
        identity_cols = st.columns(2)
        with identity_cols[0]:
            username = st.text_input(
                "Username",
                value="" if selected_user is None else selected_user["username"],
                disabled=selected_user is not None,
                key=f"admin_user_username_{form_key_suffix}",
            )
        with identity_cols[1]:
            display_name = st.text_input(
                "Display Name",
                value="" if selected_user is None else selected_user.get("display_name", ""),
                key=f"admin_user_display_name_{form_key_suffix}",
            )
        access_cols = st.columns([3, 1])
        with access_cols[0]:
            role = st.selectbox(
                "Role",
                sorted(ROLES),
                index=sorted(ROLES).index(selected_user["role"]) if selected_user else 0,
                key=f"admin_user_role_{form_key_suffix}",
            )
        with access_cols[1]:
            st.markdown("<div style='height: 1.65rem'></div>", unsafe_allow_html=True)
            active = st.checkbox(
                "Active",
                value=True if selected_user is None else bool(selected_user.get("active", True)),
                key=f"admin_user_active_{form_key_suffix}",
            )
        permission_cols = st.columns([3, 1])
        with permission_cols[0]:
            qa_signoff_permission = st.checkbox(
                "Allow QA Completion Signoff",
                value=PERMISSION_QA_SIGNOFF in (selected_user or {}).get("permissions", []),
                help="Allows this user to perform final QA signoff for assigned team/release scope.",
                key=f"admin_user_permission_qa_signoff_{form_key_suffix}",
            )
        with permission_cols[1]:
            st.caption("Admin receives signoff permission automatically on save.")
        available_teams = list_teams()
        existing_scope = selected_user.get("team_scope", ["*"]) if selected_user else []
        all_teams_label = "All Teams"
        team_scope_options = [all_teams_label, *available_teams]
        default_team_scope = (
            [all_teams_label]
            if "*" in existing_scope or (selected_user is None and role == ROLE_ADMIN)
            else [team for team in existing_scope if team in available_teams]
        )
        selected_team_scope = st.multiselect(
            "Team Scope",
            team_scope_options,
            default=default_team_scope,
            placeholder="Search and select teams",
            help="Choose All Teams for wildcard access, or choose one or more specific teams.",
            key=f"admin_user_team_scope_{form_key_suffix}",
        )
        all_teams = all_teams_label in selected_team_scope
        selected_teams = [team for team in selected_team_scope if team != all_teams_label]
        if all_teams:
            st.caption("All current and future teams are included for this user.")
        else:
            st.caption(f"{len(selected_teams)} team(s) selected.")
        password = st.text_input(
            "Password",
            type="password",
            help="Required for new users. Leave blank when editing to keep the existing password.",
            key=f"admin_user_password_{form_key_suffix}",
        )
        submitted = st.form_submit_button("Save User", type="primary", use_container_width=True)

    if action_status:
        st.success(f"Last admin action: {action_status}")

    if submitted:
        try:
            if all_teams and selected_teams:
                raise ValueError("Choose either All Teams or specific teams, not both.")
            if not all_teams and not selected_teams:
                raise ValueError("Select at least one team, or choose All Teams.")
            saved = upsert_user(
                username=username if selected_user is None else selected_user["username"],
                password=password or None,
                display_name=display_name.strip() or username,
                role=role,
                team_scope=["*"] if all_teams else selected_teams,
                permissions=[PERMISSION_QA_SIGNOFF] if qa_signoff_permission or role == ROLE_ADMIN else [],
                active=active,
                actor=current_user().get("username", "admin"),
                db_path=DEFAULT_USER_DB,
            )
            action_name = "created" if selected_user is None else "updated"
            st.session_state["admin_user_action_status"] = f"User {saved['username']} {action_name} successfully."
            st.session_state["admin_user_reset_after_save"] = True
            st.rerun()
        except Exception as exc:
            error_message = f"User was not saved: {exc}"
            st.session_state["admin_user_action_status"] = error_message
            st.error(error_message)

    if selected_user and selected_user["username"] != current_user().get("username"):
        delete_confirmed = st.checkbox(
            f"Confirm delete user {selected_user['username']}",
            help="Use delete only for accounts created by mistake. Deactivate is preferred when history should remain visible.",
            key=f"admin_user_delete_confirm_{selected_user['username']}",
        )
        cols = st.columns(3)
        if cols[0].button("Deactivate Selected User", disabled=not selected_user.get("active", True), use_container_width=True):
            set_user_active(selected_user["username"], False, current_user().get("username", "admin"), DEFAULT_USER_DB)
            st.session_state["admin_user_action_status"] = (
                f"User {selected_user['username']} deactivated successfully. Login is now disabled."
            )
            st.session_state["admin_user_reset_after_save"] = True
            st.rerun()
        if cols[1].button("Reactivate Selected User", disabled=selected_user.get("active", True), use_container_width=True):
            set_user_active(selected_user["username"], True, current_user().get("username", "admin"), DEFAULT_USER_DB)
            st.session_state["admin_user_action_status"] = (
                f"User {selected_user['username']} reactivated successfully. Login is now enabled."
            )
            st.session_state["admin_user_reset_after_save"] = True
            st.rerun()
        if cols[2].button("Delete Selected User", disabled=not delete_confirmed, use_container_width=True):
            deleted = delete_user(selected_user["username"], current_user().get("username", "admin"), DEFAULT_USER_DB)
            st.session_state["admin_user_action_status"] = (
                f"User {selected_user['username']} deleted successfully."
                if deleted
                else f"User {selected_user['username']} was not found."
            )
            st.session_state["admin_user_reset_after_save"] = True
            st.rerun()

    with st.expander("User Audit Events", expanded=False):
        audit_rows = list_user_audit(DEFAULT_USER_DB, limit=100)
        if audit_rows:
            st.dataframe(style_operational_table(pd.DataFrame(audit_rows)), use_container_width=True, hide_index=True)
        else:
            st.info("No user audit events recorded yet.")


def save_uploaded_release_inputs(team_name: str, release_line: str, files: dict[str, bytes]) -> tuple[bool, str, list[Path]]:
    team = re.sub(r"[^A-Za-z0-9._-]+", "-", str(team_name or "").strip().replace("\\", "-").replace("/", "-")).strip(".-_")
    release = re.sub(r"[^A-Za-z0-9._-]+", "-", str(release_line or "").strip().replace("\\", "-").replace("/", "-")).strip(".-_")
    if not team or team == DEFAULT_TEAM_LABEL:
        return False, "Enter a team name such as SourceOne, DPS, Avamar, or PackageTeam.", []
    if not release:
        return False, "Enter a concrete product version or release line such as 7.2.11.", []
    if "software.yml" not in files:
        return False, "software.yml is required before a release can be used by workflows.", []

    target_root = BASE_DIR / "Input" / "teams" / team / "releases" / release
    target_root.mkdir(parents=True, exist_ok=True)
    saved_paths: list[Path] = []
    for filename, content in files.items():
        if filename not in {"software.yml", "sample_version.pdf", "testcaseRepository.xlsx"}:
            continue
        target = target_root / filename
        target.write_bytes(content)
        saved_paths.append(target)

    st.session_state["active_team"] = team
    st.session_state["active_release_line"] = release
    return True, f"Input files saved for {team} / {release}.", saved_paths


def render_input_upload(embedded: bool = False) -> None:
    if current_role() not in {ROLE_ADMIN, ROLE_RELEASE_ENGINEER, ROLE_QA_ENGINEER}:
        render_access_denied("Administrator, Release Engineer, or QA Engineer")
        return

    if not embedded:
        section_title("Input Upload", "Upload release input files for a team and product version.")
    else:
        st.markdown("**Upload release input files for a team and product version.**")
    upload_status = st.session_state.pop("input_upload_status", None)
    if upload_status:
        st.success(upload_status)

    known_teams = list_teams()
    team_options = sorted(set(known_teams + ["Avamar", "DPS", "PackageTeam", "SourceOne"]))
    with st.form("release_input_upload_form"):
        team = st.selectbox(
            "Team / Product Stream",
            team_options,
            index=team_options.index(active_team_name()) if active_team_name() in team_options else 0,
        )
        custom_team = st.text_input("New Team Name", placeholder="Use only if the team is not listed")
        release_line = st.text_input("Product Version / Release Line", value=active_release_line(team), placeholder="Example: 7.2.11")
        uploaded_files = st.file_uploader(
            "Input Files",
            type=["yml", "yaml", "pdf", "xlsx"],
            accept_multiple_files=True,
            help="software.yml is mandatory. Optional files: sample_version.pdf and testcaseRepository.xlsx.",
        )
        st.caption("Required: software.yml. Optional: sample_version.pdf, testcaseRepository.xlsx.")
        submitted = st.form_submit_button("Save Input Files", type="primary", use_container_width=True)

    if submitted:
        selected_team = (custom_team.strip() or team).strip()
        files: dict[str, bytes] = {}
        for uploaded_file in uploaded_files or []:
            filename = uploaded_file.name
            if filename in {"software.yml", "software.yaml"}:
                files["software.yml"] = uploaded_file.getvalue()
            elif filename == "sample_version.pdf":
                files["sample_version.pdf"] = uploaded_file.getvalue()
            elif filename == "testcaseRepository.xlsx":
                files["testcaseRepository.xlsx"] = uploaded_file.getvalue()

        success, message, saved_paths = save_uploaded_release_inputs(selected_team, release_line, files)
        if success:
            saved_list = ", ".join(path.relative_to(BASE_DIR).as_posix() for path in saved_paths)
            st.session_state["input_upload_status"] = f"{message} Saved files: {saved_list}."
            st.rerun()
        else:
            st.error(message)


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
    page = render_sidebar(base_config, workflow_status, last_scan)
    render_app_header(page, workflow_status, last_scan)

    if page == "Dashboard":
        render_dashboard_page(current_df, comparison_df, vuln_df, metrics_df)
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
    elif page == "Compatibility Check":
        render_compatibility_check(compatibility_df)
    elif page == "QA Validation":
        render_qa_validation(qa_df)
    elif page == "Vulnerability Assessment":
        render_vulnerabilities(vuln_df)
    elif page == "Workflow Monitor":
        render_workflow(metrics_df)
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
