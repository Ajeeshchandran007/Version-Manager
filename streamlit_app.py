from __future__ import annotations

import asyncio
import json
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
from App.workspace import (
    BASE_DIR,
    CURRENT_RELEASE_DISPLAY_LABEL,
    CURRENT_RELEASE_LABEL,
    DEFAULT_TEAM_LABEL,
    OUTPUT_DIR,
    RELEASE_OUTPUT_KEYS,
    active_config,
    active_output_path,
    active_release_name,
    active_team_name,
    allowed_teams_for_user,
    config_path_for_result,
    create_release_snapshot,
    create_team_snapshot,
    list_releases,
    project_path,
    release_display_label,
    release_output_dir,
    release_value_from_display,
    team_input_software_path,
)
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
    save_workspace_outputs,
)
from Utils.software_loader import load_software, load_software_metadata
from Utils.utils import load_config, logger
from agent.memory import get_run_history as read_run_history
from agent.memory import log_audit
from agent.multi_agent import LangGraphVersionManager
from mcp_server import _load_json, _resolve_current_version, _save_json, _vulnerability_path


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
    "Release Workspace",
    "Software Inventory",
    "Latest Versions",
    "Version Comparison",
    "Compatibility Check",
    "Workflow Monitor",
    "Reports",
]
SECURITY_PAGES = ["Vulnerability Assessment", "Cache Analytics"]
RELEASE_PAGES = ["Release Comparison", "Package Readiness"]
QA_PAGES = ["QA Validation"]
ADMIN_PAGES = ["Audit Logs", "Settings"]

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
    notes: str,
    test_date: Any,
    tested_by: str,
    evidence_file: Any | None,
) -> dict[str, Any]:
    qa_file = active_output_path("qa_validation.json")
    evidence_dir = qa_file.parent / "qa_evidence"
    data = load_json(str(qa_file), file_mtime(qa_file))
    if software_name not in data:
        raise ValueError(f"QA record not found for {software_name}")

    evidence_path = ""
    if evidence_file is not None:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(ch if ch.isalnum() or ch in {".", "-", "_"} else "_" for ch in evidence_file.name)
        target = evidence_dir / f"{software_name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_name}"
        target.write_bytes(evidence_file.getbuffer())
        evidence_path = str(target)

    record = data[software_name]
    record["Installation Status"] = installation_status
    record["Test Result"] = test_result
    record["Test Notes"] = notes.strip() or record.get("Test Notes", "")
    record["Test Date"] = str(test_date)
    record["Tested By"] = tested_by.strip() or current_user().get("username", "unknown")
    record["Manual QA Updated"] = True
    record["Last QA Update"] = datetime.now().isoformat(timespec="seconds")
    if evidence_path:
        record["Evidence File"] = evidence_path

    checks = record.get("Functional Validation") or {}
    if test_result == "PASS":
        record["Functional Validation"] = {key: True for key in checks} if checks else {
            "Application Launch": True,
            "Service Running": True,
            "Registry Verified": True,
            "Files Installed": True,
            "Environment Variables": True,
            "License Activated": True,
        }
    elif test_result == "FAIL":
        record["Functional Validation"] = {key: False for key in checks} if checks else {}

    qa_file.parent.mkdir(parents=True, exist_ok=True)
    qa_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    clear_dashboard_cache()
    return record


def run_async(coro: Any) -> Any:
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def runtime_state() -> dict[str, Any]:
    config = active_config(load_config())
    return {
        "config": config,
        "version_fetcher": VersionFetcher(),
        "pdf_reader": PDFReader(config),
        "server_querier": ServerQuerier(),
        "vulnerability_checker": VulnerabilityChecker(),
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


async def trigger_full_pipeline(category: str, force_refresh: bool) -> dict[str, Any]:
    state = runtime_state()
    workflow = LangGraphVersionManager(build_streamlit_agent_tools(state))
    final_state = await workflow.run(
        "Run the full software version, security, package readiness, compatibility, QA validation, and reporting workflow.",
        category=category,
        force_refresh=force_refresh,
    )
    comparison = final_state.get("comparison_results", {})
    vulnerabilities = final_state.get("vulnerability_results", {})
    report_package = final_state.get("report_package", {})
    notification = report_package.get("notification", {})
    excel = report_package.get("excel", {})
    updates = [name for name, result in comparison.items() if is_actionable_update(result)]
    return {
        "operation": "full_pipeline",
        "workflow": "LangGraph Supervisor",
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
    if current_cu and latest_cu and current_cu != latest_cu:
        return "CU Gap"
    current_major = current_version.split(".")[0]
    latest_major = latest_version.split(".")[0]
    if current_major and latest_major and current_major != latest_major:
        return "Major Gap"
    return "Minor Gap"


def update_priority(gap: str, risk: str) -> str:
    risk = risk.upper()
    if risk in {"CRITICAL", "HIGH"}:
        return risk.title()
    if gap in {"Major Gap", "CU Gap"}:
        return "Medium"
    if gap == "Minor Gap":
        return "Low"
    return "None"


def ensure_workspace_outputs(
    comparison: dict[str, Any],
    latest: dict[str, Any],
    vulnerabilities: dict[str, Any],
    vendor_requirements: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    config = active_config(load_config())
    readiness, qa_validation = save_workspace_outputs(
        comparison,
        latest,
        vulnerabilities,
        str(project_path(config["output_files"].get("package_readiness_json", "output/package_readiness.json"))),
        str(project_path(config["output_files"].get("qa_validation_json", "output/qa_validation.json"))),
        load_software_metadata(config["input_files"]["software_yml"], "ALL"),
        vendor_requirements,
    )
    if comparison:
        save_testcase_impact_outputs(
            comparison,
            str((BASE_DIR / config["input_files"].get("testcase_repository_xlsx", "Input/testcaseRepository.xlsx")).resolve()),
            str(project_path(config["output_files"].get("testcase_impact_json", "output/testcase_impact.json"))),
            str(project_path(config["output_files"].get("testcase_impact_xlsx", "output/Test_Case_Impact_Assessment.xlsx"))),
        )
    return readiness, qa_validation


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
                "Current Version": value(record, "Build Version", "version"),
                "Current CU": value(record, "Cumulative Update (CU)", "cu", default=""),
                "Server Name": "Configured Server" if value(record, "source") == "live server" else "PDF Inventory",
                "Environment": "Production",
                "Last Scanned": format_ts(value(record, "last_scanned", default="")) if value(record, "last_scanned", default="") else fallback_scan_time,
                "Source": value(record, "source", default="Unknown"),
            }
        )
    return pd.DataFrame(rows)


def normalize_latest(data: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for name, record in data.items():
        metadata = record.get("cache_metadata", {})
        rows.append(
            {
                "Software Name": name,
                "Vendor": vendor_for(name),
                "Latest Version": value(record, "Build Version", "version"),
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
        current_version = value(current, "Build Version", "version")
        latest_version = value(latest, "Build Version", "version")
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
                "Status": "Outdated" if needs_update else "Up-to-date",
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
                "Current Installed Version": value(record, "current_version", "version"),
                "Latest Available Version": value(record, "latest_version", default=""),
                "Version Assessed": value(record, "version_assessed", default="current"),
                "CVE Severity": value(record, "severity", default="UNKNOWN").upper(),
                "Risk Level": value(record, "risk_level", default="UNKNOWN").upper(),
                "CVE Count": len(cves),
                "Security Assessment": value(record, "assessment", default="No assessment available."),
                "Source": value(record, "source", default="Unknown"),
            }
        )
    return pd.DataFrame(rows)


def normalize_package_readiness(data: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for name, record in data.items():
        rows.append(
            {
                "Software Name": value(record, "Software Name", default=name),
                "Vendor": value(record, "Vendor", default=vendor_for(name)),
                "Current Version": value(record, "Current Version"),
                "Target Version": value(record, "Target Version"),
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
        rows.append(
            {
                "Software Name": value(record, "Software Name", default=name),
                "Current Version": value(record, "Current Version"),
                "Latest Version": value(record, "Target Version", "Package Version"),
                "Package Version": value(record, "Package Version"),
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
                "Test Notes": value(record, "Test Notes"),
                "Test Date": value(record, "Test Date", default=""),
                "Tested By": value(record, "Tested By", default=""),
                "Evidence File": value(record, "Evidence File", default=""),
            }
        )
    return pd.DataFrame(rows)


def normalize_testcase_impact(data: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for item in data.get("test_case_plan", []) or []:
        rows.append({
            "Software Name": value(item, "Software Name"),
            "Current Version": value(item, "Current Version"),
            "Target Version": value(item, "Target Version"),
            "Test Coverage": value(item, "Test Coverage", default="Not Found"),
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
    st.markdown(
        f"""
        <div class="vm-title">
            <h1>{title}</h1>
            <p>{subtitle}</p>
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
        if text in {"NO", "UP-TO-DATE", "NONE"}:
            return "background-color: #E7F6ED; color: #135D31; font-weight: 700; border: 1px solid #B9E3C8;"
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
        active_release = active_release_name()
        st.markdown("### Version Manager")
        st.caption("Software posture and remediation operations")
        st.markdown(
            f"""
            <div class="vm-sidebar-card">
                <div class="vm-sidebar-kv">Signed In<strong>{user.get("display_name", user.get("username", "Unknown"))}</strong></div>
                <div class="vm-sidebar-kv">Role<strong>{current_role()}</strong></div>
                <div class="vm-sidebar-kv">Project<strong>Version Manager</strong></div>
                <div class="vm-sidebar-kv">Team<strong>{active_team}</strong></div>
                <div class="vm-sidebar-kv">Release<strong>{release_display_label(active_release)}</strong></div>
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

    if operation == "full_pipeline":
        if current_role() == ROLE_QA_ENGINEER:
            title = "Validation Workflow Completed"
            summary = "Compatibility and QA validation outputs were refreshed by the controlled backend workflow."
        else:
            title = "Full Pipeline Completed"
            summary = "Latest versions, current inventory, comparison, vulnerability assessment, Excel output, and email notification were processed."
        cards = [
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
        cols = st.columns(min(len(cards), 4))
        for col, (label, val, help_text) in zip(cols, cards):
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
        trigger_send_report_email=trigger_send_report_email,
        validate_cron_expression=validate_cron_expression,
        value=value,
        visible_output_files_for_role=visible_output_files_for_role,
        with_actor=with_actor,
    )


def render_operations(config: dict[str, Any]) -> None:
    app_pages.render_operations(config, page_context())


def render_dashboard(current_df: pd.DataFrame, comparison_df: pd.DataFrame, vuln_df: pd.DataFrame, metrics_df: pd.DataFrame) -> None:
    app_pages.render_dashboard(current_df, comparison_df, vuln_df, metrics_df, page_context())


def render_dashboard_page(current_df: pd.DataFrame, comparison_df: pd.DataFrame, vuln_df: pd.DataFrame, metrics_df: pd.DataFrame) -> None:
    app_pages.render_dashboard_page(current_df, comparison_df, vuln_df, metrics_df, page_context())


def release_summary_rows(team: str) -> list[dict[str, Any]]:
    return app_pages.release_summary_rows(team, page_context())


def render_release_workspace(config: dict[str, Any]) -> None:
    app_pages.render_release_workspace(config, page_context())


def render_context_selector(location: str = "dashboard") -> None:
    app_pages.render_context_selector(page_context(), location)


def render_inventory(current_df: pd.DataFrame) -> None:
    section_title("Software Inventory", "Installed software inventory from live servers and document extraction.")
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
    section_title("Package Readiness", "Release engineering workspace for package preparation, vendor review, and upgrade impact.")
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


def load_release_output(release: str, filename: str, team: str | None = None) -> dict[str, Any]:
    team = team or active_team_name()
    if release == CURRENT_RELEASE_LABEL:
        path = team_workspace_output_dir(team) / filename
    else:
        path = release_output_dir(release, team) / filename
    return load_json(str(path), file_mtime(path))


def vulnerability_risk(record: dict[str, Any]) -> str:
    return str(value(record, "risk_level", "Risk Level", "risk", default="UNKNOWN")).upper()


def render_release_comparison() -> None:
    if current_role() not in {ROLE_ADMIN, ROLE_RELEASE_ENGINEER}:
        render_access_denied("Administrator or Release Engineer")
        return
    section_title("Release Comparison", "Compare version drift, vulnerability movement, and readiness between release baselines.")
    teams = list_teams()
    selected_team = st.selectbox(
        "Team / Product Stream",
        teams,
        index=teams.index(active_team_name()) if active_team_name() in teams else 0,
    )
    releases = [CURRENT_RELEASE_LABEL, *list_releases(selected_team)]
    if len(releases) < 2:
        st.info(f"Create at least one release baseline to compare it with {CURRENT_RELEASE_DISPLAY_LABEL}.")
        return

    col1, col2 = st.columns(2)
    release_display_options = [release_display_label(release) for release in releases]
    with col1:
        base_release_display = st.selectbox("Base Release", release_display_options, index=0)
        base_release = release_value_from_display(base_release_display)
    with col2:
        target_default = 1 if len(releases) > 1 else 0
        target_release_display = st.selectbox("Target Release", release_display_options, index=target_default)
        target_release = release_value_from_display(target_release_display)

    if base_release == target_release:
        st.warning("Select two different releases.")
        return

    base_comparison = load_release_output(base_release, "comparison_report.json", selected_team)
    target_comparison = load_release_output(target_release, "comparison_report.json", selected_team)
    base_vulnerabilities = load_release_output(base_release, "vulnerability_report.json", selected_team)
    target_vulnerabilities = load_release_output(target_release, "vulnerability_report.json", selected_team)

    base_updates = {name for name, record in base_comparison.items() if is_actionable_update(record)}
    target_updates = {name for name, record in target_comparison.items() if is_actionable_update(record)}
    resolved_updates = sorted(base_updates - target_updates)
    new_updates = sorted(target_updates - base_updates)
    carried_updates = sorted(base_updates & target_updates)

    base_risky = {
        name
        for name, record in base_vulnerabilities.items()
        if vulnerability_risk(record) in {"CRITICAL", "HIGH", "MEDIUM"}
    }
    target_risky = {
        name
        for name, record in target_vulnerabilities.items()
        if vulnerability_risk(record) in {"CRITICAL", "HIGH", "MEDIUM"}
    }
    resolved_risk = sorted(base_risky - target_risky)
    new_risk = sorted(target_risky - base_risky)
    carried_risk = sorted(base_risky & target_risky)

    metric_cols = st.columns(6)
    metric_cols[0].metric("Base Updates", len(base_updates))
    metric_cols[1].metric("Target Updates", len(target_updates))
    metric_cols[2].metric("Resolved Updates", len(resolved_updates))
    metric_cols[3].metric("New Updates", len(new_updates))
    metric_cols[4].metric("Resolved Risk", len(resolved_risk))
    metric_cols[5].metric("New Risk", len(new_risk))

    rows = []
    for name in sorted(set(base_comparison) | set(target_comparison)):
        base_record = base_comparison.get(name, {})
        target_record = target_comparison.get(name, {})
        rows.append(
            {
                "Software Name": name,
                "Base Current": value(base_record, "Current Version", "current_version", default="Not available"),
                "Base Target": value(base_record, "Latest Version", "Target Version", default="Not available"),
                "Target Current": value(target_record, "Current Version", "current_version", default="Not available"),
                "Target Target": value(target_record, "Latest Version", "Target Version", default="Not available"),
                "Update Movement": (
                    "Resolved" if name in resolved_updates else
                    "New" if name in new_updates else
                    "Carried Forward" if name in carried_updates else
                    "No Action"
                ),
                "Base Risk": vulnerability_risk(base_vulnerabilities.get(name, {})),
                "Target Risk": vulnerability_risk(target_vulnerabilities.get(name, {})),
                "Risk Movement": (
                    "Resolved" if name in resolved_risk else
                    "New" if name in new_risk else
                    "Carried Forward" if name in carried_risk else
                    "No Material Risk"
                ),
            }
        )

    searchable_table(
        pd.DataFrame(rows),
        "release_comparison",
        ["Update Movement", "Risk Movement", "Base Risk", "Target Risk"],
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
    section_title("QA Validation", "Installation verification, functional checks, package verification, and deployment test status.")
    if qa_df.empty:
        st.info("No QA validation data found. Run version comparison first.")
        return
    qa_df = add_environment_readiness(qa_df)
    result_counts = qa_df["Test Result"].value_counts().to_dict()
    cols = st.columns(4)
    cols[0].metric("PASS", result_counts.get("PASS", 0))
    cols[1].metric("FAIL", result_counts.get("FAIL", 0))
    cols[2].metric("WARNING", result_counts.get("WARNING", 0))
    cols[3].metric("NOT TESTED", result_counts.get("NOT TESTED", 0))
    columns = [
        "Software Name",
        "Package Version",
        "Installation Status",
        "Test Result",
        "Environment Readiness",
        "Test Case Count",
        "Test Coverage",
        "Test Date",
        "Tested By",
        "Test Notes",
        "Evidence File",
    ]
    testcase_impact_file = active_output_path("testcase_impact.json")
    testcase_impact_excel_file = active_output_path("Test_Case_Impact_Assessment.xlsx")
    impact = load_json(str(testcase_impact_file), file_mtime(testcase_impact_file))
    impacted = impact.get("impacted_software", {}) if isinstance(impact, dict) else {}
    qa_df["Test Case Count"] = qa_df["Software Name"].map(lambda name: impacted.get(name, {}).get("Test Case Count", 0))
    qa_df["Test Coverage"] = qa_df["Software Name"].map(lambda name: impacted.get(name, {}).get("Test Coverage", "Not Required"))
    searchable_table(qa_df[columns], "qa_validation", ["Installation Status", "Test Result", "Environment Readiness"])

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
            "Test Coverage",
            "Test Case ID",
            "Test Case Name",
            "Test Type",
            "Priority",
            "Automation Status",
            "Owner",
            "Applicable Version",
        ]
        searchable_table(testcase_df[display_cols], "testcase_impact", ["Test Coverage", "Priority", "Test Type", "Owner"])
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
    with st.form("manual_qa_update_form"):
        form_cols = st.columns([1.2, 1, 1])
        software_name = form_cols[0].selectbox("Software", qa_df["Software Name"].tolist())
        installation_status = form_cols[1].selectbox(
            "Installation Status",
            ["Not Tested", "Installed Successfully", "Failed", "Rollback Completed", "Pending Restart", "No Deployment Required"],
        )
        test_result = form_cols[2].selectbox(
            "Test Result",
            ["NOT TESTED", "PASS", "FAIL", "WARNING", "BASELINE VERIFIED"],
        )
        notes = st.text_area("QA Notes", placeholder="Add install result, validation notes, known issues, or rollback details.")
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
                notes,
                test_date,
                tested_by,
                evidence_file,
            )
            st.success(f"QA result saved for {software_name}.")
            st.rerun()
        except Exception as exc:
            st.error(f"QA result was not saved: {exc}")


def render_vulnerabilities(vuln_df: pd.DataFrame) -> None:
    section_title("Vulnerability Assessment", "Security assessment of current installed versions with latest version context.")
    if vuln_df.empty:
        st.info("No vulnerability data found.")
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

    st.subheader("Access Control")
    st.caption("Administrator manages settings and users. Release Engineer prepares assessments and reports. QA Engineer validates deployments without security/CVE views.")
    user_rows = [
        {
            "User": user["username"],
            "Display Name": user.get("display_name", user["username"]),
            "Role": user["role"],
            "Team Scope": ", ".join(user.get("team_scope", ["*"])),
            "Can Run Scans": "Yes" if user["role"] in ACTION_ROLES else "No",
            "Can Manage Settings": "Yes" if user["role"] in ADMIN_ROLES else "No",
        }
        for user in configured_users(config)
    ]
    st.dataframe(style_operational_table(pd.DataFrame(user_rows)), use_container_width=True, hide_index=True)


def main() -> None:
    inject_css()
    base_config = load_config()
    if not require_login(base_config, allowed_teams_for_user, CURRENT_RELEASE_LABEL):
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
    package_readiness, qa_validation = ensure_workspace_outputs(comparison, latest, vulnerabilities, vendor_requirements)
    readiness_df = normalize_package_readiness(package_readiness)
    qa_df = normalize_qa_validation(qa_validation)

    workflow_status = "Completed" if not comparison_df.empty else "Not Run"
    last_scan = last_scan_time(latest, vulnerabilities)
    page = render_sidebar(base_config, workflow_status, last_scan)
    render_app_header(page, workflow_status, last_scan)

    if page == "Dashboard":
        render_dashboard_page(current_df, comparison_df, vuln_df, metrics_df)
    elif page == "Release Workspace":
        render_release_workspace(base_config)
    elif page == "Operations":
        render_operations(config)
    elif page == "Software Inventory":
        render_inventory(current_df)
    elif page == "Latest Versions":
        render_latest(latest_df)
    elif page == "Version Comparison":
        render_comparison(comparison_df)
    elif page == "Release Comparison":
        render_release_comparison()
    elif page == "Package Readiness":
        render_package_readiness(readiness_df)
    elif page == "Compatibility Check":
        render_compatibility_check(qa_df)
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
    elif page == "Settings":
        render_settings(config)


if __name__ == "__main__":
    main()
