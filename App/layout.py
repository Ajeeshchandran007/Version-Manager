from __future__ import annotations

import streamlit as st

from App.auth import current_role, current_user



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




def render_app_header(page: str, workflow_status: str, last_scan: str) -> None:
    status_tone = "ok" if workflow_status == "Completed" else "warn"
    compact_last_scan = last_scan if last_scan == "Not available" else last_scan[5:]
    user = current_user()
    role_label = current_role()
    st.markdown(
        f"""
        <div class="vm-shell-header">
            <div>
                <h1>Enterprise Product Release AI Advisory Platform</h1>
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




def render_access_denied(required: str) -> None:
    st.warning(f"This page requires {required} access. Your current role is {current_role()}.")


