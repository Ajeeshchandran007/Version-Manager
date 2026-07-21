from __future__ import annotations

import json
import re
from html import escape
from collections.abc import Callable
from typing import Any

import pandas as pd
import streamlit as st
from openai import AsyncOpenAI
from tavily import TavilyClient

from App.auth import ROLE_ADMIN, ROLE_QA_ENGINEER, ROLE_RELEASE_ENGINEER, current_role, current_user
from App.qa_signoff import load_qa_signoff
from App.workspace import WORKSPACES_DIR, active_config, active_output_path, active_release_line, active_team_name, team_input_software_path
from Core.ai_governance import apply_final_governance, classify_web_search_prompt
from Core.assistant_tool_router import ToolRouteDecision, resolve_assistant_tool
from Core.pdf_reader import PDFReader
from Core.server_querier import ServerQuerier
from Core.version_fetcher import VersionFetcher
from Utils.software_loader import load_software
from Utils.utils import load_config, logger
from agent.context import build_release_context, context_from_app
from agent.planner import AssistantPlanner
from agent.verifier import append_verification_note, verify_assistant_response


ROLE_ASSISTANT_PAGES = {
    ROLE_ADMIN: "AI Assistant",
    ROLE_RELEASE_ENGINEER: "Release Assistant",
    ROLE_QA_ENGINEER: "QA Assistant",
}


def inject_assistant_css() -> None:
    st.markdown(
        """
        <style>
        .vm-claude-page {
            max-width: 980px;
            margin: 0 auto;
            padding: 4px 6px 84px;
        }
        .vm-claude-topbar {
            display: flex;
            justify-content: space-between;
            align-items: start;
            gap: 14px;
            margin: 2px 0 26px;
        }
        .vm-claude-title {
            color: var(--vm-text);
            font-size: 0.98rem;
            font-weight: 750;
            margin: 0;
        }
        .vm-claude-title span {
            color: var(--vm-muted);
            font-size: 0.86rem;
            font-weight: 650;
            margin-left: 6px;
        }
        .vm-claude-badges {
            display: flex;
            flex-wrap: wrap;
            gap: 7px;
            justify-content: flex-end;
        }
        .vm-claude-badge {
            border: 1px solid #d9d3c7;
            background: #f4f1eb;
            color: #403b35;
            border-radius: 999px;
            padding: 5px 10px;
            font-size: 0.72rem;
            font-weight: 750;
            white-space: nowrap;
        }
        .vm-claude-context-wrap {
            max-width: 760px;
            margin: 0 auto 18px;
        }
        .vm-claude-context {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 12px;
            margin: 18px auto 20px;
        }
        .vm-claude-stat {
            background: rgba(255,255,255,0.03);
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 10px;
            padding: 12px 14px;
        }
        .vm-claude-stat-label {
            color: var(--vm-muted);
            font-size: 0.76rem;
            font-weight: 650;
        }
        .vm-claude-stat-value {
            color: var(--vm-text);
            font-size: 1.12rem;
            font-weight: 800;
            margin-top: 5px;
            overflow-wrap: anywhere;
        }
        .vm-claude-thread {
            max-width: 760px;
            margin: 0 auto;
            min-height: 360px;
        }
        .vm-claude-empty {
            margin-top: 46px;
            color: var(--vm-muted);
            font-size: 0.92rem;
            line-height: 1.5;
        }
        .vm-claude-empty strong {
            color: var(--vm-text);
            font-size: 1rem;
        }
        .vm-claude-suggestions {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 16px;
        }
        .vm-claude-suggestion {
            border: 1px solid rgba(255,255,255,0.08);
            background: rgba(255,255,255,0.04);
            color: var(--vm-text);
            border-radius: 999px;
            padding: 8px 12px;
            font-size: 0.82rem;
        }
        .vm-claude-row {
            display: flex;
            margin: 18px 0;
        }
        .vm-claude-row.user {
            justify-content: flex-end;
        }
        .vm-claude-row.assistant {
            justify-content: flex-start;
        }
        .vm-claude-bubble {
            max-width: min(680px, 86%);
            border-radius: 12px;
            padding: 12px 15px;
            font-size: 0.96rem;
            line-height: 1.55;
            white-space: pre-wrap;
            overflow-wrap: anywhere;
        }
        .vm-claude-row.user .vm-claude-bubble {
            background: #f0ede8;
            color: #25211d;
            border: 1px solid #e5dfd6;
            box-shadow: 0 8px 20px rgba(0,0,0,0.16);
        }
        .vm-claude-row.assistant .vm-claude-bubble {
            background: transparent;
            color: var(--vm-text);
            border: 0;
            padding-left: 0;
        }
        .vm-claude-answer-label {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            color: #a8a098;
            font-size: 0.76rem;
            font-weight: 650;
            margin-bottom: 9px;
        }
        .vm-claude-answer-label::before {
            content: "";
            width: 7px;
            height: 7px;
            border-radius: 999px;
            background: #d7c8ae;
        }
        .vm-claude-actions {
            max-width: 760px;
            margin: 0 auto 12px;
            display: flex;
            justify-content: flex-end;
        }
        .vm-qa-widget {
            margin: 8px 0 24px;
            max-width: 680px;
        }
        .vm-qa-widget-intro {
            border-left: 3px solid #d8c7aa;
            padding-left: 12px;
            margin-bottom: 18px;
        }
        .vm-qa-widget-note {
            color: #a49b91;
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0;
            margin-bottom: 7px;
        }
        .vm-qa-widget-title {
            color: #f7f4ef;
            font-size: 1.02rem;
            line-height: 1.45;
            margin-bottom: 0;
        }
        .vm-qa-widget-title strong {
            color: #ffffff;
        }
        .vm-qa-context-pill {
            display: inline-flex;
            gap: 8px;
            align-items: center;
            border: 1px solid rgba(255,255,255,0.08);
            background: rgba(255,255,255,0.04);
            color: #d8d0c6;
            border-radius: 999px;
            padding: 5px 10px;
            font-size: 0.76rem;
            font-weight: 700;
            margin-bottom: 18px;
        }
        .vm-qa-metrics {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 18px;
            margin: 0 0 22px;
        }
        .vm-qa-metric {
            min-height: 62px;
        }
        .vm-qa-metric-label {
            color: #9a9288;
            font-size: 0.76rem;
            margin-bottom: 4px;
        }
        .vm-qa-metric-value {
            color: #f7f4ef;
            font-size: 1.45rem;
            font-weight: 800;
            line-height: 1.15;
        }
        .vm-qa-signoff-card {
            border: 1px solid #ded8cf;
            background: #fffdfa;
            color: #25211d;
            border-radius: 10px;
            padding: 16px 18px;
            box-shadow: 0 10px 26px rgba(0,0,0,0.16);
        }
        .vm-qa-signoff-head {
            display: flex;
            justify-content: space-between;
            gap: 12px;
            align-items: center;
            margin-bottom: 14px;
        }
        .vm-qa-signoff-title {
            font-size: 0.92rem;
            font-weight: 800;
        }
        .vm-qa-signoff-pill {
            border-radius: 8px;
            background: #ffd991;
            color: #5f3a00;
            padding: 5px 10px;
            font-size: 0.74rem;
            font-weight: 700;
            white-space: nowrap;
        }
        .vm-qa-signoff-row {
            display: grid;
            grid-template-columns: 1fr auto;
            gap: 16px;
            padding: 5px 0;
            font-size: 0.82rem;
            border-bottom: 1px solid #eee8df;
        }
        .vm-qa-signoff-row:last-child {
            border-bottom: 0;
        }
        .vm-qa-signoff-label {
            color: #68615a;
        }
        .vm-qa-signoff-value {
            color: #25211d;
            font-weight: 750;
            text-align: right;
        }
        div[data-testid="stChatInput"] {
            background: rgba(7,13,20,0.96);
            border-top: 1px solid rgba(255,255,255,0.06);
        }
        div[data-testid="stChatInput"] textarea {
            background: #fbfaf8 !important;
            border: 1px solid #e2ddd4 !important;
            color: #28231f !important;
            border-radius: 14px !important;
            min-height: 52px !important;
            box-shadow: 0 12px 30px rgba(0,0,0,0.22);
        }
        div[data-testid="stChatInput"] textarea::placeholder {
            color: #77716a !important;
        }
        @media (max-width: 900px) {
            .vm-claude-topbar,
            .vm-claude-context {
                grid-template-columns: 1fr;
                display: grid;
            }
            .vm-claude-badges {
                justify-content: flex-start;
            }
            .vm-claude-bubble {
                max-width: 94%;
            }
            .vm-qa-metrics {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            .vm-qa-signoff-head,
            .vm-qa-signoff-row {
                grid-template-columns: 1fr;
            }
            .vm-qa-signoff-value {
                text-align: left;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def assistant_page_name(role: str | None = None) -> str:
    return ROLE_ASSISTANT_PAGES.get(role or current_role(), "AI Assistant")


def records_for_assistant(df: pd.DataFrame, columns: list[str], limit: int = 12) -> list[dict[str, Any]]:
    if df.empty:
        return []
    available = [column for column in columns if column in df.columns]
    if not available:
        return []
    return df[available].head(limit).fillna("").to_dict(orient="records")


def build_assistant_context(
    current_df: pd.DataFrame,
    comparison_df: pd.DataFrame,
    vuln_df: pd.DataFrame,
    readiness_df: pd.DataFrame,
    qa_df: pd.DataFrame,
    metrics_df: pd.DataFrame,
) -> dict[str, Any]:
    role = current_role()
    context: dict[str, Any] = {
        "user": current_user().get("username", ""),
        "role": role,
        "team": active_team_name(),
        "release": active_release_line(),
        "output_dir": str(active_output_path("__placeholder__").parent),
        "inventory_count": len(current_df),
    }
    if not comparison_df.empty:
        needs_update = comparison_df[comparison_df.get("Need Update", "") == "Yes"] if "Need Update" in comparison_df.columns else pd.DataFrame()
        context["version_summary"] = {
            "records": len(comparison_df),
            "needs_update": len(needs_update),
            "top_updates": records_for_assistant(
                needs_update if not needs_update.empty else comparison_df,
                ["Software Name", "Current Version", "Latest Version", "Version Gap", "Update Priority", "Risk Level"],
            ),
        }
    if role in {ROLE_ADMIN, ROLE_RELEASE_ENGINEER} and not readiness_df.empty:
        context["package_readiness"] = records_for_assistant(
            readiness_df,
            ["Software Name", "Readiness", "Package Status", "Owner", "Blockers", "Recommendation"],
        )
    if role in {ROLE_ADMIN, ROLE_QA_ENGINEER} and not qa_df.empty:
        context["qa_validation"] = records_for_assistant(
            qa_df,
            [
                "Software Name",
                "Installation Status",
                "Test Result",
                "Tested By",
                "Test Cases Passed",
                "Test Cases Failed",
                "Test Notes",
            ],
        )
    if role in {ROLE_ADMIN, ROLE_RELEASE_ENGINEER} and not vuln_df.empty:
        context["vulnerabilities"] = {
            "risk_counts": vuln_df["Risk Level"].value_counts().to_dict() if "Risk Level" in vuln_df.columns else {},
            "top_findings": records_for_assistant(
                vuln_df,
                ["Software Name", "Current Version", "Risk Level", "CVE Count", "Highest Severity", "Recommendation"],
            ),
        }
    if role == ROLE_ADMIN and not metrics_df.empty:
        context["workflow_activity"] = records_for_assistant(
            metrics_df.sort_values("timestamp", ascending=False) if "timestamp" in metrics_df.columns else metrics_df,
            ["timestamp", "event", "status", "duration_seconds", "details"],
            limit=8,
        )
    return context


def _qa_summary(qa_df: pd.DataFrame) -> dict[str, int]:
    if qa_df.empty:
        return {"executed": 0, "total": 0, "passed": 0, "failed": 0}
    total = len(qa_df)
    status = qa_df.get("Test Result", pd.Series(dtype=str)).fillna("").astype(str).str.lower()
    executed = int(status.ne("").sum())
    passed = int(status.str.contains("pass").sum())
    failed = int(status.str.contains("fail").sum())
    return {"executed": executed, "total": total, "passed": passed, "failed": failed}


def _format_signed_date(value: Any) -> str:
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else (text or "Not signed")


def _qa_widget_requested(prompt: str) -> bool:
    prompt_lower = prompt.lower()
    return any(term in prompt_lower for term in ("qa dashboard", "qa status", "qa summary", "signoff", "sign-off", "test status"))


def _recommended_testcase_requested(prompt: str) -> bool:
    prompt_lower = prompt.lower()
    test_terms = ("test case", "testcase", "test coverage", "coverage")
    action_terms = (
        "recommend",
        "recommended",
        "how many",
        "count",
        "total",
        "no coverage",
        "no testcase coverage",
        "no test case coverage",
        "without coverage",
        "not covered",
        "missing coverage",
    )
    return any(term in prompt_lower for term in test_terms) and any(term in prompt_lower for term in action_terms)


def _current_release_requested(prompt: str) -> bool:
    prompt_lower = prompt.lower()
    return "release" in prompt_lower and any(term in prompt_lower for term in ("current", "active", "selected", "which", "what"))


def _tested_by_requested(prompt: str) -> bool:
    prompt_lower = prompt.lower()
    return any(
        term in prompt_lower
        for term in (
            "who tested",
            "tested by",
            "tester",
            "who validated",
            "validated by",
            "who executed",
        )
    )


def _latest_version_requested(prompt: str) -> bool:
    prompt_lower = prompt.lower()
    return any(term in prompt_lower for term in ("latest version", "latest build", "current latest", "newest version")) or (
        "latest" in prompt_lower and "version" in prompt_lower
    )


def _current_version_requested(prompt: str) -> bool:
    prompt_lower = prompt.lower()
    if _latest_version_requested(prompt):
        return False
    return any(term in prompt_lower for term in ("current version", "installed version", "existing version", "deployed version"))


def _package_readiness_requested(prompt: str) -> bool:
    prompt_lower = prompt.lower()
    return any(
        term in prompt_lower
        for term in (
            "package readiness",
            "package ready",
            "packaging readiness",
            "ready for package",
            "ready for packaging",
            "ready to package",
            "package blocker",
            "packaging blocker",
            "blocked package",
            "blocked packaging",
            "package checklist",
            "pending checklist",
            "checklist pending",
            "checklist is pending",
        )
    ) or ("checklist" in prompt_lower and "pending" in prompt_lower)


def _internal_package_prompt(prompt: str) -> bool:
    prompt_lower = prompt.lower()
    context_terms = ("sourceone", "dps", "release", "package", "packaging", active_team_name().lower(), active_release_line().lower())
    checklist_terms = ("checklist", "pending steps", "remaining steps", "next steps")
    return _package_readiness_requested(prompt) or (
        any(term in prompt_lower for term in checklist_terms)
        and any(term for term in context_terms if term and term in prompt_lower)
    ) or (
        any(term in prompt_lower for term in ("ready", "blocked", "status"))
        and any(term for term in context_terms if term and term in prompt_lower)
        and any(term in prompt_lower for term in ("package", "packaging"))
    )


def _vulnerability_requested(prompt: str) -> bool:
    prompt_lower = prompt.lower()
    return any(
        term in prompt_lower
        for term in (
            "vulnerability",
            "vulnerabilities",
            "cve",
            "security risk",
            "security assessment",
            "risk level",
            "release blocker",
            "security blocker",
            "war room",
            "risk score",
            "security signoff",
        )
    )


def _reports_requested(prompt: str) -> bool:
    prompt_lower = prompt.lower()
    return any(
        term in prompt_lower
        for term in (
            "artifact",
            "artifacts",
            "release artifact",
            "release artifacts",
            "available report",
            "reports available",
            "reports are available",
            "what reports",
            "output files",
            "generated files",
            "download report",
            "report status",
        )
    )


def _match_software_name(prompt: str, candidates: list[str]) -> str:
    prompt_lower = prompt.lower()
    normalized_prompt = _normalize_lookup_text(prompt_lower)
    best = ""
    for name in candidates:
        name_lower = str(name or "").lower()
        if name_lower and name_lower in prompt_lower:
            return name
        normalized_name = _normalize_lookup_text(name_lower)
        if normalized_name and normalized_name in normalized_prompt:
            best = name
    if best:
        return best
    for name in candidates:
        tokens = [token for token in _normalize_lookup_text(str(name)).split() if len(token) >= 3]
        if tokens and all(token in normalized_prompt for token in tokens[:2]):
            return name
    return ""


def _latest_summary_requested(prompt: str) -> bool:
    prompt_lower = prompt.lower()
    summary_terms = (
        "all latest",
        "list latest",
        "show latest",
        "latest software versions",
        "latest versions",
        "software version used",
        "software versions used",
        "versions for this release",
        "latest for this release",
    )
    return any(term in prompt_lower for term in summary_terms)


def _specific_latest_software_query(prompt: str, candidates: list[str]) -> bool:
    if not _latest_version_requested(prompt) or _latest_summary_requested(prompt):
        return False
    normalized_prompt = _normalize_lookup_text(prompt)
    for token in ("latest", "version", "build", "newest", "current"):
        normalized_prompt = re.sub(rf"\b{re.escape(token)}\b", " ", normalized_prompt)
    active_team = _normalize_lookup_text(active_team_name())
    active_release = _normalize_lookup_text(active_release_line())
    for context_token in (active_team, active_release, "sourceone", "dps", "release", "line"):
        if context_token:
            normalized_prompt = normalized_prompt.replace(context_token, " ")
    remaining_tokens = [token for token in normalized_prompt.split() if len(token) >= 3]
    if remaining_tokens:
        return True
    return bool(_match_software_name(prompt, candidates))


def _normalize_lookup_text(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else " " for ch in str(value or "")).strip()


def _answer_latest_from_outputs(prompt: str) -> dict[str, str] | None:
    if not _latest_version_requested(prompt):
        return None
    latest, latest_path = _load_best_output_json("latest_versions.json", prompt)
    comparison, comparison_path = _load_best_output_json("comparison_report.json", prompt)
    candidates = sorted({*latest.keys(), *comparison.keys()})
    software = _match_software_name(prompt, candidates)
    if not software:
        if _specific_latest_software_query(prompt, candidates):
            return None
        return _answer_latest_summary_from_outputs(latest, latest_path, comparison, comparison_path)

    record = latest.get(software) if isinstance(latest.get(software), dict) else {}
    if not record and isinstance(comparison.get(software), dict):
        record = comparison[software].get("latest", {}) if isinstance(comparison[software].get("latest"), dict) else {}
    version = _first_value(record, "Build Version", "version", "Latest Version")
    cu = _first_value(record, "Cumulative Update (CU)", "cu", "Latest CU")
    if not version:
        return None

    source_path = latest_path or comparison_path
    team, release = _context_label_from_output_path(source_path)
    message = f"For **{team} / {release}**, the latest version of **{software}** in generated output is **{version}**."
    if cu and str(cu).lower() not in {"not found", "none", "null"}:
        message += f" CU: **{cu}**."
    if source_path:
        message += f"\n\nSource: `{source_path}`"
    return {"content": message, "source": "Used MCP tool: Latest Version Output", "widget": ""}


def _answer_latest_summary_from_outputs(
    latest: dict[str, Any],
    latest_path: str,
    comparison: dict[str, Any],
    comparison_path: str,
) -> dict[str, str] | None:
    rows: list[str] = []
    source_path = latest_path or comparison_path
    records = latest if latest else {
        name: record.get("latest", {})
        for name, record in comparison.items()
        if isinstance(record, dict) and isinstance(record.get("latest"), dict)
    }
    for name, record in list(records.items())[:12]:
        if not isinstance(record, dict):
            continue
        version = _first_value(record, "Build Version", "version", "Latest Version")
        cu = _first_value(record, "Cumulative Update (CU)", "cu", "Latest CU")
        if not version:
            continue
        suffix = f" ({cu})" if cu and cu.lower() not in {"not found", "none", "null"} else ""
        rows.append(f"- **{name}**: {version}{suffix}")
    if not rows:
        return None
    team, release = _context_label_from_output_path(source_path)
    message = f"Latest software versions available in generated output for **{team} / {release}**:\n\n" + "\n".join(rows)
    if len(records) > len(rows):
        message += f"\n\nShowing {len(rows)} of {len(records)} software item(s)."
    if source_path:
        message += f"\n\nSource: `{source_path}`"
    return {"content": message, "source": "Used MCP tool: Latest Version Output", "widget": ""}


def _answer_current_from_outputs(prompt: str) -> dict[str, str] | None:
    if not _current_version_requested(prompt):
        return None
    current, current_path = _load_best_output_json("current_versions.json", prompt)
    comparison, comparison_path = _load_best_output_json("comparison_report.json", prompt)
    candidates = sorted({*current.keys(), *comparison.keys()})
    software = _match_software_name(prompt, candidates)
    if not software:
        return None

    record = current.get(software) if isinstance(current.get(software), dict) else {}
    if not record and isinstance(comparison.get(software), dict):
        record = comparison[software].get("current", {}) if isinstance(comparison[software].get("current"), dict) else {}
    version = _first_value(record, "Build Version", "version", "Current Version")
    cu = _first_value(record, "Cumulative Update (CU)", "cu", "Current CU")
    source = _first_value(record, "source", "Source")
    if not version:
        return None

    source_path = current_path or comparison_path
    team, release = _context_label_from_output_path(source_path)
    message = f"For **{team} / {release}**, the current installed version of **{software}** in generated output is **{version}**."
    if cu and str(cu).lower() not in {"not found", "none", "null"}:
        message += f" CU: **{cu}**."
    if source:
        message += f"\n\nInventory source: **{source}**."
    if source_path:
        message += f"\n\nSource: `{source_path}`"
    return {"content": message, "source": "Used MCP tool: Current Version Output", "widget": ""}


def _answer_package_readiness_from_outputs(prompt: str) -> dict[str, str] | None:
    if not _package_readiness_requested(prompt):
        return None
    if current_role() == ROLE_QA_ENGINEER:
        return _deny_package_readiness_for_qa()
    readiness, source_path = _load_best_output_json("package_readiness.json", prompt)
    if not readiness:
        return None

    candidates = sorted(str(name) for name in readiness.keys())
    software = _match_software_name(prompt, candidates)
    team, release = _context_label_from_output_path(source_path)
    if software and isinstance(readiness.get(software), dict):
        record = readiness[software]
        status = _first_value(record, "Package Readiness", "status") or "Not Assessed"
        impact = _first_value(record, "Upgrade Impact", "impact") or "Not recorded"
        blocker = _first_value(record, "Blocker", "blocker")
        owner = _first_value(record, "Owner", "owner") or "Application Owner"
        message = (
            f"For **{team} / {release}**, package readiness for **{software}** is **{status}**.\n\n"
            f"- **Upgrade impact**: {impact}\n"
            f"- **Owner**: {owner}"
        )
        if blocker:
            message += f"\n- **Blocker**: {blocker}"
    else:
        counts: dict[str, int] = {}
        blockers: list[tuple[str, str]] = []
        for name, record in readiness.items():
            if not isinstance(record, dict):
                continue
            status = _first_value(record, "Package Readiness", "status") or "Not Assessed"
            counts[status] = counts.get(status, 0) + 1
            blocker = _first_value(record, "Blocker", "blocker")
            if blocker:
                blockers.append((str(name), str(blocker)))
        rows = "\n".join(f"- **{status}**: {count}" for status, count in sorted(counts.items()))
        message = f"Package readiness summary for **{team} / {release}**:\n\n{rows or '- No readiness statuses recorded.'}"
        if blockers:
            message += "\n\nBlocked or review-needed software:\n" + "\n".join(
                f"- **{name}**: {blocker}" for name, blocker in blockers[:10]
            )
    if source_path:
        message += f"\n\nSource: `{source_path}`"
    return {"content": message, "source": "Used MCP tool: Package Readiness", "widget": ""}


def _deny_package_readiness_for_qa() -> dict[str, str]:
    return {
        "content": (
            "Package readiness is owned by Release Assistant and is not available in QA Assistant.\n\n"
            "In QA Assistant, I can help with QA validation, testcase coverage, signoff readiness, compatibility checks, "
            "current/latest versions, tester details, and QA reports."
        ),
        "source": "Access guardrail: QA role",
        "widget": "",
    }


def _answer_missing_package_readiness(prompt: str) -> dict[str, str] | None:
    if not _package_readiness_requested(prompt):
        return None
    if current_role() == ROLE_QA_ENGINEER:
        return _deny_package_readiness_for_qa()
    team = active_team_name()
    release = active_release_line(team)
    return {
        "content": (
            f"No package readiness output is available for **{team} / {release}**. "
            "Run the release workflow or ask Release Assistant to generate package readiness before I answer packaging status from project evidence."
        ),
        "source": "Used MCP tool: Package Readiness",
        "widget": "",
    }


def _answer_vulnerability_from_outputs(prompt: str) -> dict[str, str] | None:
    if not _vulnerability_requested(prompt):
        return None
    intelligence, intelligence_path = _load_best_output_json("vulnerability_intelligence.json", prompt)
    if intelligence and isinstance(intelligence.get("findings"), list):
        return _answer_vulnerability_intelligence(prompt, intelligence, intelligence_path)
    vulnerabilities, source_path = _load_best_output_json("vulnerability_report.json", prompt)
    if not vulnerabilities:
        return None

    candidates = sorted(str(name) for name in vulnerabilities.keys())
    software = _match_software_name(prompt, candidates)
    team, release = _context_label_from_output_path(source_path)
    if software and isinstance(vulnerabilities.get(software), dict):
        record = vulnerabilities[software]
        risk = _first_value(record, "risk_level", "Risk Level") or "UNKNOWN"
        severity = _first_value(record, "severity", "CVE Severity") or "UNKNOWN"
        cves = record.get("cves") if isinstance(record.get("cves"), list) else []
        assessment = _first_value(record, "assessment", "Security Assessment") or "No assessment recorded."
        message = (
            f"For **{team} / {release}**, vulnerability posture for **{software}** is **{risk.upper()}** risk "
            f"with **{severity.upper()}** highest severity and **{len(cves)} CVE(s)**.\n\n{assessment}"
        )
    else:
        risk_counts: dict[str, int] = {}
        total_cves = 0
        high_items: list[str] = []
        for name, record in vulnerabilities.items():
            if not isinstance(record, dict):
                continue
            risk = (_first_value(record, "risk_level", "Risk Level") or "UNKNOWN").upper()
            risk_counts[risk] = risk_counts.get(risk, 0) + 1
            cves = record.get("cves") if isinstance(record.get("cves"), list) else []
            total_cves += len(cves)
            if risk in {"CRITICAL", "HIGH"}:
                high_items.append(str(name))
        order = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE", "UNKNOWN"]
        rows = "\n".join(f"- **{risk}**: {risk_counts[risk]}" for risk in order if risk in risk_counts)
        message = f"Vulnerability assessment summary for **{team} / {release}**:\n\n{rows or '- No risk levels recorded.'}\n\nTotal CVEs: **{total_cves}**."
        if high_items:
            message += "\n\nHigh-priority security review queue:\n" + "\n".join(f"- **{name}**" for name in high_items[:10])
    if source_path:
        message += f"\n\nSource: `{source_path}`"
    return {"content": message, "source": "Used MCP tool: Vulnerability Assessment", "widget": ""}


def _vulnerability_evidence_note(intelligence: dict[str, Any], source_path: str) -> str:
    evidence = intelligence.get("evidence_source", {}) if isinstance(intelligence.get("evidence_source"), dict) else {}
    if not evidence:
        return f"\n\nEvidence source: **EPRA vulnerability intelligence** | Source file: `{source_path}`" if source_path else ""

    active_source = evidence.get("active_source") or "EPRA vulnerability intelligence"
    trust_level = evidence.get("trust_level") or "Unknown"
    fallback_used = "Yes" if evidence.get("fallback_used") else "No"
    source_file = evidence.get("source_file") or source_path

    note = (
        f"\n\nEvidence source: **{active_source}** | "
        f"Trust: **{trust_level}** | "
        f"Fallback used: **{fallback_used}**"
    )
    if source_file:
        note += f" | Source file: `{source_file}`"
    return note


def _answer_vulnerability_intelligence(prompt: str, intelligence: dict[str, Any], source_path: str) -> dict[str, str]:
    findings = [item for item in intelligence.get("findings", []) if isinstance(item, dict)]
    candidates = sorted({str(item.get("software_name")) for item in findings if item.get("software_name")})
    software = _match_software_name(prompt, candidates)
    team, release = _context_label_from_output_path(source_path)
    if software:
        scoped = [item for item in findings if str(item.get("software_name", "")).lower() == software.lower()]
        scoped = sorted(scoped, key=lambda item: int(item.get("release_risk_score") or 0), reverse=True)
        rows = "\n".join(
            f"- **{item.get('cve') or item.get('finding_id')}**: {item.get('severity', 'UNKNOWN')} | "
            f"score {item.get('release_risk_score', 0)} | {item.get('blocker_decision', 'Review')} | "
            f"{item.get('recommended_action', 'Review with Security.')}"
            for item in scoped[:8]
        )
        message = f"Vulnerability intelligence for **{software}** in **{team} / {release}**:\n\n{rows or '- No findings recorded.'}"
    else:
        summary = intelligence.get("summary", {}) if isinstance(intelligence.get("summary"), dict) else {}
        blockers = [item for item in findings if item.get("release_blocker")]
        message = (
            f"Release-aware vulnerability intelligence for **{team} / {release}**:\n\n"
            f"- **Total normalized findings**: {summary.get('total_findings', len(findings))}\n"
            f"- **Release blockers**: {summary.get('release_blockers', len(blockers))}\n"
            f"- **Critical findings**: {summary.get('severity_counts', {}).get('CRITICAL', 0)}\n"
            f"- **High findings**: {summary.get('severity_counts', {}).get('HIGH', 0)}"
        )
        if blockers:
            message += "\n\nTop release blockers:\n" + "\n".join(
                f"- **{item.get('software_name')}** {item.get('cve') or item.get('finding_id')}: "
                f"score {item.get('release_risk_score', 0)} - {item.get('recommended_action', 'Review with Security.')}"
                for item in blockers[:8]
            )
    message += _vulnerability_evidence_note(intelligence, source_path)
    return {"content": message, "source": "Used MCP tool: Vulnerability Intelligence", "widget": ""}


def _answer_reports_from_outputs(prompt: str) -> dict[str, str] | None:
    if not _reports_requested(prompt):
        return None
    output_dir = active_output_path("__placeholder__").parent
    report_files = [
        ("Latest Versions", output_dir / "latest_versions.json"),
        ("Current Versions", output_dir / "current_versions.json"),
        ("Version Comparison", output_dir / "comparison_report.json"),
        ("Vulnerability Report", output_dir / "vulnerability_report.json"),
        ("Package Readiness", output_dir / "package_readiness.json"),
        ("QA Validation", output_dir / "qa_validation.json"),
        ("Test Case Impact", output_dir / "testcase_impact.json"),
        ("Excel Assessment", output_dir / "Software_Version_Assessment.xlsx"),
        ("Test Case Impact Excel", output_dir / "Test_Case_Impact_Assessment.xlsx"),
    ]
    available = [(label, path) for label, path in report_files if path.exists()]
    team = active_team_name()
    release = active_release_line(team)
    if not available:
        return {
            "content": f"No generated report files are available yet for **{team} / {release}**. Run the workflow to create release outputs.",
            "source": "Used MCP tool: Release Reports",
            "widget": "",
        }
    rows = "\n".join(f"- **{label}**: `{path}`" for label, path in available)
    return {
        "content": f"Generated report files available for **{team} / {release}**:\n\n{rows}",
        "source": "Used MCP tool: Release Reports",
        "widget": "",
    }


def _first_value(record: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _context_label_from_output_path(source_path: str) -> tuple[str, str]:
    path_text = str(source_path or "")
    parts = path_text.replace("\\", "/").split("/")
    try:
        releases_index = parts.index("releases")
        team = parts[releases_index - 1]
        release = parts[releases_index + 1]
        return team, release
    except (ValueError, IndexError):
        team = active_team_name()
        return team, active_release_line(team)


async def _latest_version_research_answer(prompt: str) -> tuple[dict[str, str] | None, str]:
    if not _latest_version_requested(prompt):
        return None, ""
    software = _match_software_name(prompt, _software_names_from_inputs_and_outputs())
    if not software:
        return None, ""
    try:
        result = await VersionFetcher().fetch(software)
    except Exception as exc:
        logger.warning("Assistant latest-version research failed: %s", exc)
        return None, f"MCP latest-version research could not run: {exc}"
    version = _first_value(result, "Build Version", "version")
    cu = _first_value(result, "Cumulative Update (CU)", "cu")
    if not version:
        return None, "MCP latest-version research did not return a version."
    message = f"The latest researched version of **{software}** is **{version}**."
    if cu and cu.lower() not in {"not found", "none", "null"}:
        message += f" CU: **{cu}**."
    source_url = _first_value(result, "Release Notes", "source_url")
    if source_url:
        message += f"\n\nSource: {source_url}"
    return {"content": message, "source": "Used MCP tool: Latest Version Research", "widget": ""}, ""


async def _current_version_mcp_answer(prompt: str) -> tuple[dict[str, str] | None, str]:
    if not _current_version_requested(prompt):
        return None, ""
    software = _match_software_name(prompt, _software_names_from_inputs_and_outputs())
    if not software:
        return None, ""

    team = active_team_name()
    release = active_release_line(team)
    config = active_config(load_config())

    try:
        server_result = await ServerQuerier(config, team=team, release_line=release).fetch(software)
    except Exception as exc:
        logger.warning("Assistant server current-version query failed: %s", exc)
        server_result = None
    if server_result and _first_value(server_result, "Build Version", "version"):
        return _format_current_lookup_answer(software, server_result, "Current Version Query"), ""

    try:
        pdf_result = await PDFReader(config).fetch(software)
    except Exception as exc:
        logger.warning("Assistant PDF current-version fallback failed: %s", exc)
        return None, f"MCP current-version lookup could not run: {exc}"
    if pdf_result and _first_value(pdf_result, "Build Version", "version"):
        return _format_current_lookup_answer(software, pdf_result, "PDF Version Fallback"), ""
    return None, "MCP current-version lookup did not return a version."


def _format_current_lookup_answer(software: str, record: dict[str, Any], tool_name: str) -> dict[str, str]:
    version = _first_value(record, "Build Version", "version")
    cu = _first_value(record, "Cumulative Update (CU)", "cu")
    source = _first_value(record, "source", "Source")
    team = active_team_name()
    release = active_release_line(team)
    message = f"For **{team} / {release}**, the current installed version of **{software}** is **{version}**."
    if cu and cu.lower() not in {"not found", "none", "null"}:
        message += f" CU: **{cu}**."
    if source:
        message += f"\n\nInventory source: **{source}**."
    return {"content": message, "source": f"Used MCP tool: {tool_name}", "widget": ""}


def _software_names_from_inputs_and_outputs() -> list[str]:
    names: set[str] = set()
    for filename in ("latest_versions.json", "comparison_report.json", "current_versions.json"):
        data, _ = _load_best_output_json(filename, "")
        names.update(str(name) for name in data.keys())
    input_path = team_input_software_path(active_team_name(), active_release_line())
    if input_path.exists():
        try:
            names.update(load_software(str(input_path), "ALL"))
        except Exception as exc:
            logger.warning("Assistant could not load software names from %s: %s", input_path, exc)
    return sorted(name for name in names if name)


def _answer_tested_by(prompt: str, qa_df: pd.DataFrame) -> str:
    if qa_df.empty:
        return "I do not have QA validation rows for the selected team/release yet. Run the QA workflow or load QA validation data first."

    prompt_lower = prompt.lower()
    rows = qa_df.copy()
    if "Software Name" in rows.columns:
        matching = rows[rows["Software Name"].fillna("").astype(str).str.lower().apply(lambda name: bool(name and name in prompt_lower))]
        if not matching.empty:
            rows = matching

    display_rows: list[str] = []
    for _, row in rows.head(20).iterrows():
        software = str(row.get("Software Name") or "Unknown software").strip()
        tested_by = str(row.get("Tested By") or "").strip()
        result = str(row.get("Test Result") or "").strip()
        tested_label = tested_by or "Not recorded"
        result_label = result or "Not tested"
        display_rows.append(f"- **{software}**: {tested_label} ({result_label})")

    if not display_rows:
        return "I found QA validation data, but no tester details are recorded for this selection."

    team = active_team_name()
    release = active_release_line()
    return f"Tester details for **{team} / {release}**:\n\n" + "\n".join(display_rows)


def _load_json_file(path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _candidate_output_paths(filename: str, prompt: str) -> list[Any]:
    prompt_lower = prompt.lower()
    exact_context_paths = _exact_context_output_paths(filename, prompt_lower)
    if exact_context_paths:
        return exact_context_paths

    candidates = [active_output_path(filename)]
    if WORKSPACES_DIR.exists():
        for path in WORKSPACES_DIR.glob(f"*/releases/*/output/{filename}"):
            text = path.as_posix().lower()
            team = path.parts[-5].lower() if len(path.parts) >= 5 else ""
            release = path.parts[-3].lower() if len(path.parts) >= 3 else ""
            if team and team in prompt_lower:
                candidates.insert(0, path)
            elif release and release in prompt_lower:
                candidates.append(path)
            else:
                candidates.append(path)
    deduped = []
    seen = set()
    for path in candidates:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            deduped.append(path)
    return deduped


def _exact_context_output_paths(filename: str, prompt_lower: str) -> list[Any]:
    if not WORKSPACES_DIR.exists():
        return []
    matches = []
    for output_dir in WORKSPACES_DIR.glob("*/releases/*/output"):
        team = output_dir.parts[-4] if len(output_dir.parts) >= 4 else ""
        release = output_dir.parts[-2] if len(output_dir.parts) >= 2 else ""
        team_matched = bool(team and team.lower() in prompt_lower)
        release_matched = bool(release and release.lower() in prompt_lower)
        if team_matched and release_matched:
            matches.append(output_dir / filename)
    return _dedupe_paths(matches)


def _dedupe_paths(paths: list[Any]) -> list[Any]:
    deduped = []
    seen = set()
    for path in paths:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            deduped.append(path)
    return deduped


def _load_best_output_json(filename: str, prompt: str) -> tuple[dict[str, Any], str]:
    for path in _candidate_output_paths(filename, prompt):
        data = _load_json_file(path)
        if data:
            return data, str(path)
    return {}, ""


def _answer_recommended_testcases(prompt: str) -> str:
    impact, source_path = _load_best_output_json("testcase_impact.json", prompt)
    summary = impact.get("summary") if isinstance(impact.get("summary"), dict) else {}
    if not summary:
        return (
            "I checked the active workspace outputs, but I could not find a generated Test Case Impact summary for this context. "
            "Confirm the selected team/release or run the QA workflow again."
        )

    total = int(summary.get("total_recommended_test_cases") or 0)
    requiring_update = int(summary.get("software_requiring_update") or 0)
    with_coverage = int(summary.get("software_with_test_coverage") or 0)
    without_coverage = int(summary.get("software_without_test_coverage") or 0)
    prompt_lower = prompt.lower()
    if any(term in prompt_lower for term in ("no coverage", "no testcase coverage", "no test case coverage", "without coverage", "not covered", "missing coverage")):
        impacted = impact.get("impacted_software") if isinstance(impact.get("impacted_software"), dict) else {}
        uncovered = [
            name
            for name, record in impacted.items()
            if isinstance(record, dict) and int(record.get("Test Case Count") or 0) == 0
        ]
        if uncovered:
            rows = "\n".join(f"- **{name}**" for name in uncovered[:20])
            return (
                f"For the current release, **{without_coverage} software item(s)** do not have mapped testcase coverage:\n\n"
                f"{rows}\n\nSource: `{source_path}`"
            )
        return (
            f"All **{with_coverage} software item(s)** requiring QA review have mapped testcase coverage. "
            f"No uncovered software is listed in the generated Test Case Impact output.\n\nSource: `{source_path}`"
        )
    return (
        f"The current release has **{total} recommended QA test cases** mapped from the test case repository.\n\n"
        f"{requiring_update} software item(s) require update review. "
        f"{with_coverage} have mapped test coverage, and {without_coverage} do not yet have mapped repository coverage.\n\n"
        f"Source: `{source_path}`"
    )


def _workspace_release_candidates(prompt: str) -> list[tuple[str, str, str]]:
    prompt_lower = prompt.lower()
    rows: list[tuple[str, str, str]] = []
    if not WORKSPACES_DIR.exists():
        return rows
    for output_dir in WORKSPACES_DIR.glob("*/releases/*/output"):
        team = output_dir.parts[-4]
        release = output_dir.parts[-2]
        score = 0
        if team.lower() in prompt_lower:
            score += 10
        if release.lower() in prompt_lower:
            score += 6
        if (output_dir / "testcase_impact.json").exists() or (output_dir / "qa_validation.json").exists():
            score += 2
        rows.append((f"{score:03d}", team, release))
    return sorted(rows, reverse=True)


def _answer_current_release(prompt: str) -> str:
    active_team = active_team_name()
    active_release = active_release_line(active_team)
    if active_release:
        return f"The active UI context is **{active_team} / {active_release}**."

    candidates = _workspace_release_candidates(prompt)
    if not candidates:
        return "I could not find an active release in the UI or generated workspace outputs."

    _, team, release = candidates[0]
    if len(candidates) == 1:
        return f"The available generated release context is **{team} / {release}**."

    other = ", ".join(f"{candidate_team} / {candidate_release}" for _, candidate_team, candidate_release in candidates[1:4])
    return f"I found **{team} / {release}** as the best matching generated release context. Other available contexts: {other}."


def _load_signoff_for_context(team: str, release: str) -> dict[str, Any]:
    if team and release:
        path = WORKSPACES_DIR / team / "releases" / release / "output" / "qa_signoff.json"
        data = _load_json_file(path)
        if data:
            return {**data, "_source_context": f"{team} / {release}", "_exact_context": True}
    active = load_qa_signoff(active_output_path("__placeholder__").parent)
    if active:
        return {**active, "_source_context": f"{active.get('product', team)} / {active.get('release_line', release)}", "_exact_context": True}
    if team and WORKSPACES_DIR.exists():
        for path in sorted(WORKSPACES_DIR.glob(f"{team}/releases/*/output/qa_signoff.json"), reverse=True):
            data = _load_json_file(path)
            if data:
                release_name = path.parts[-3]
                return {**data, "_source_context": f"{team} / {release_name}", "_exact_context": False}
    return {}


def _tool_first_answer(prompt: str, qa_df: pd.DataFrame) -> dict[str, str] | None:
    release_context = build_release_context(
        team=active_team_name(),
        release=active_release_line() or "",
        role=current_role(),
        user=str(current_user().get("username") or ""),
        output_dir=active_output_path("__placeholder__").parent,
    )
    semantic_answer = _semantic_tool_answer(prompt, qa_df)
    if semantic_answer:
        return semantic_answer

    if _recommended_testcase_requested(prompt):
        return {
            "content": _answer_recommended_testcases(prompt),
            "source": "Used MCP tool: Test Case Impact",
            "widget": "",
        }
    for answer_builder in (
        _answer_package_readiness_from_outputs,
        _answer_vulnerability_from_outputs,
        _answer_reports_from_outputs,
    ):
        answer = answer_builder(prompt)
        if answer:
            return answer
    missing_package_answer = _answer_missing_package_readiness(prompt)
    if missing_package_answer:
        return missing_package_answer

    planner = AssistantPlanner(release_context)
    qa_records = qa_df.fillna("").to_dict(orient="records") if not qa_df.empty else []
    planned_result = planner.answer(prompt, qa_records)
    if planned_result:
        verification = verify_assistant_response(planned_result, release_context)
        return {
            "content": append_verification_note(planned_result.message, verification),
            "source": (planned_result.source or "Used MCP tool").replace("Used app tool", "Used MCP tool"),
            "widget": planned_result.widget,
        }

    current_output = _answer_current_from_outputs(prompt)
    if current_output:
        return current_output

    latest_output = _answer_latest_from_outputs(prompt)
    if latest_output:
        return latest_output
    if _tested_by_requested(prompt):
        return {
            "content": _answer_tested_by(prompt, qa_df),
            "source": "Used MCP tool: QA Tester Details",
            "widget": "",
        }
    if _current_release_requested(prompt):
        return {
            "content": _answer_current_release(prompt),
            "source": "Used MCP tool: Release Context",
            "widget": "",
        }
    if _qa_widget_requested(prompt):
        return {
            "content": "I prepared a compact QA dashboard snapshot from the current release data.",
            "source": "Used MCP tool: QA Validation",
            "widget": "qa_dashboard",
        }
    return None


def _semantic_tool_answer(prompt: str, qa_df: pd.DataFrame) -> dict[str, str] | None:
    route = resolve_assistant_tool(
        prompt,
        role=current_role(),
        team=active_team_name(),
        release=active_release_line(),
        config=load_config(),
    )
    if route.selected_tool and not route.allowed:
        return {
            "content": _semantic_denied_message(route),
            "source": route.source_label or f"Access guardrail: {current_role()}",
            "widget": "",
        }
    if not route.allowed or not route.selected_tool:
        return None

    answer = _answer_for_semantic_route(route, prompt, qa_df)
    if answer:
        answer.setdefault("source", route.source_label)
        answer["routing"] = f"{route.method}:{route.confidence:.2f}"
        return answer
    if route.selected_tool in {"release_reports", "package_readiness", "vulnerability_assessment"}:
        return _missing_internal_tool_answer(route)
    return None


def _answer_for_semantic_route(route: ToolRouteDecision, prompt: str, qa_df: pd.DataFrame) -> dict[str, str] | None:
    if route.selected_tool == "release_reports":
        return _answer_reports_from_outputs(prompt)
    if route.selected_tool == "package_readiness":
        return _answer_package_readiness_from_outputs(prompt) or _answer_missing_package_readiness(prompt)
    if route.selected_tool == "vulnerability_assessment":
        return _answer_vulnerability_from_outputs(prompt)
    if route.selected_tool == "testcase_impact":
        return {"content": _answer_recommended_testcases(prompt), "source": "Used MCP tool: Test Case Impact", "widget": ""}
    if route.selected_tool == "current_version":
        return _answer_current_from_outputs(prompt)
    if route.selected_tool == "latest_version":
        return _answer_latest_from_outputs(prompt)
    if route.selected_tool == "release_context":
        return {"content": _answer_current_release(prompt), "source": "Used MCP tool: Release Context", "widget": ""}
    if route.selected_tool == "qa_validation":
        if _tested_by_requested(prompt):
            return {"content": _answer_tested_by(prompt, qa_df), "source": "Used MCP tool: QA Tester Details", "widget": ""}
        return {
            "content": "I prepared a compact QA dashboard snapshot from the current release data.",
            "source": "Used MCP tool: QA Validation",
            "widget": "qa_dashboard",
        }
    return None


def _semantic_denied_message(route: ToolRouteDecision) -> str:
    if route.selected_tool == "package_readiness" and current_role() == ROLE_QA_ENGINEER:
        return _deny_package_readiness_for_qa()["content"]
    if route.selected_tool == "release_reports" and current_role() == ROLE_QA_ENGINEER:
        return (
            "Generic release artifacts are owned by Release Assistant and are not available in QA Assistant.\n\n"
            "In QA Assistant, I can help with QA validation artifacts, testcase impact, signoff readiness, "
            "compatibility checks, tester details, and QA reports."
        )
    return route.denied_reason or f"This request is not available for {current_role()}."


def _missing_internal_tool_answer(route: ToolRouteDecision) -> dict[str, str]:
    team = active_team_name()
    release = active_release_line(team)
    labels = {
        "release_reports": "release artifacts or report files",
        "package_readiness": "package readiness output",
        "vulnerability_assessment": "vulnerability assessment output",
    }
    label = labels.get(route.selected_tool, "generated output")
    return {
        "content": f"No {label} is available for **{team} / {release}**. Run the relevant workflow to generate this evidence before I answer from project data.",
        "source": route.source_label or "Used MCP tool",
        "widget": "",
    }


def _web_search_answer(prompt: str) -> tuple[dict[str, str] | None, str]:
    config = load_config()
    api_key = str(config.get("tavily_api_key") or "").strip()
    if not api_key or api_key.startswith("${"):
        return None, "Web search is not configured. Add TAVILY_API_KEY to enable internet-backed answers."
    try:
        response = TavilyClient(api_key=api_key).search(
            query=prompt,
            max_results=5,
            include_answer=True,
        )
    except Exception as exc:
        logger.warning("Assistant web search failed: %s", exc)
        return None, f"Web search failed: {exc}"

    content = _format_web_search_answer(prompt, response)
    if not content:
        return None, "Web search did not return usable results."
    return {"content": content, "source": "Used web search", "widget": ""}, ""


def _web_search_allowed(prompt: str) -> tuple[bool, str]:
    decision = classify_web_search_prompt(
        prompt,
        team=active_team_name(),
        release=active_release_line(),
        known_software=_software_names_from_inputs_and_outputs(),
    )
    if decision.allowed:
        return True, ""
    return False, f"Web search skipped by guardrail. {decision.reason}"


def _format_web_search_answer(prompt: str, response: dict[str, Any]) -> str:
    answer = str(response.get("answer") or "").strip()
    results = response.get("results") if isinstance(response.get("results"), list) else []
    lines: list[str] = []
    if answer:
        lines.append(answer)
    elif results:
        lines.append(f"I found web results for **{prompt}**.")
    else:
        return ""

    source_lines = []
    for result in results[:3]:
        if not isinstance(result, dict):
            continue
        title = str(result.get("title") or "Source").strip()
        url = str(result.get("url") or "").strip()
        content = str(result.get("content") or "").strip()
        detail = content[:180].rstrip()
        if url and detail:
            source_lines.append(f"- **{title}**: {detail} ({url})")
        elif url:
            source_lines.append(f"- **{title}**: {url}")
    if source_lines:
        lines.append("\nSources:\n" + "\n".join(source_lines))
    return "\n\n".join(lines)


def _render_qa_dashboard_widget(qa_summary: dict[str, int], app_context: dict[str, Any], signoff: dict[str, Any]) -> None:
    total = qa_summary["total"]
    coverage = round((qa_summary["executed"] / total) * 100, 1) if total else 0
    team = escape(str(active_team_name() or "selected team"))
    release = escape(str(active_release_line() or app_context.get("release") or "selected release"))
    status = str(signoff.get("status") or "Not signed off")
    status_label = status.replace("QA ", "").replace("With", "with").strip()
    signed_by = escape(str(signoff.get("signed_by") or "Not signed"))
    signed_date = escape(_format_signed_date(signoff.get("signed_date")))
    signoff_context = escape(str(signoff.get("_source_context") or f"{active_team_name()} / {release}"))
    signoff_context_label = "Signoff source" if signoff.get("_exact_context") is False else "Signoff context"
    st.markdown(
        f"""
        <div class="vm-qa-widget">
            <div class="vm-qa-widget-intro">
                <div class="vm-qa-widget-note">Prepared QA dashboard snapshot</div>
                <div class="vm-qa-widget-title">Here is the current QA view for <strong>{team}</strong>.</div>
            </div>
            <div class="vm-qa-context-pill">Release <strong>{release}</strong></div>
            <div class="vm-qa-metrics">
                <div class="vm-qa-metric">
                    <div class="vm-qa-metric-label">Coverage</div>
                    <div class="vm-qa-metric-value">{coverage}%</div>
                </div>
                <div class="vm-qa-metric">
                    <div class="vm-qa-metric-label">Executed / total</div>
                    <div class="vm-qa-metric-value">{qa_summary["executed"]} / {qa_summary["total"]}</div>
                </div>
                <div class="vm-qa-metric">
                    <div class="vm-qa-metric-label">Pass / fail</div>
                    <div class="vm-qa-metric-value">{qa_summary["passed"]} / {qa_summary["failed"]}</div>
                </div>
                <div class="vm-qa-metric">
                    <div class="vm-qa-metric-label">Software items</div>
                    <div class="vm-qa-metric-value">{app_context.get("inventory_count", 0)}</div>
                </div>
            </div>
            <div class="vm-qa-signoff-card">
                <div class="vm-qa-signoff-head">
                    <div class="vm-qa-signoff-title">QA sign-off</div>
                    <div class="vm-qa-signoff-pill">{escape(status_label)}</div>
                </div>
                <div class="vm-qa-signoff-row">
                    <div class="vm-qa-signoff-label">Signed by</div>
                    <div class="vm-qa-signoff-value">{signed_by}</div>
                </div>
                <div class="vm-qa-signoff-row">
                    <div class="vm-qa-signoff-label">Signed date</div>
                    <div class="vm-qa-signoff-value">{signed_date}</div>
                </div>
                <div class="vm-qa-signoff-row">
                    <div class="vm-qa-signoff-label">{signoff_context_label}</div>
                    <div class="vm-qa-signoff-value">{signoff_context}</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_message(
    role: str,
    content: str,
    qa_summary: dict[str, int],
    app_context: dict[str, Any],
    signoff: dict[str, Any],
    widget: str = "",
    source: str = "",
) -> None:
    safe_content = escape(content).replace("\n", "<br>")
    label_text = escape(source or "Enterprise Product Release AI Advisory Platform")
    label = f"<div class='vm-claude-answer-label'>{label_text}</div>" if role == "assistant" else ""
    st.markdown(
        f"""
        <div class="vm-claude-row {escape(role)}">
            <div class="vm-claude-bubble">
                {label}{safe_content}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if role == "assistant" and widget == "qa_dashboard":
        _render_qa_dashboard_widget(qa_summary, app_context, signoff)


def _assistant_empty_state_copy(role: str) -> dict[str, Any]:
    if role == ROLE_ADMIN:
        return {
            "description": (
                "The assistant can summarize release readiness, version status, package status, "
                "security risks, blockers, and report status."
            ),
            "suggestions": [
                "Show release dashboard summary",
                "What needs admin attention?",
                "Are there any blockers?",
            ],
        }
    if role == ROLE_RELEASE_ENGINEER:
        return {
            "description": (
                "The assistant can summarize release readiness, version drift, package status, "
                "blockers, security risks, and report status."
            ),
            "suggestions": [
                "Show release readiness summary",
                "What packages are blocked?",
                "What updates need action?",
            ],
        }
    return {
        "description": "The assistant can summarize QA readiness, impacted tests, blockers, and signoff status.",
        "suggestions": [
            "Show QA dashboard summary",
            "What should QA focus on?",
            "Is this ready for signoff?",
        ],
    }


async def ask_assistant(messages: list[dict[str, str]], app_context: dict[str, Any]) -> str:
    config = load_config()
    api_key = str(config.get("openai_api_key") or "").strip()
    if not api_key or api_key.startswith("${"):
        return "OpenAI is not configured. Add OPENAI_API_KEY in the environment or config before using the assistant."
    role = app_context.get("role", "")
    role_rules = {
        ROLE_ADMIN: "You may discuss admin, workflow, release, QA, vulnerability, and reporting context.",
        ROLE_RELEASE_ENGINEER: "Do not reveal admin-only workflow monitor or audit details. Focus on release readiness, package status, versions, vulnerabilities, and reports.",
        ROLE_QA_ENGINEER: "Do not reveal admin-only workflow monitor, audit details, or package-owner-only operational details. Focus on QA validation, test impact, signoff readiness, versions, and reports.",
    }
    system_prompt = (
        "You are the Enterprise Product Release AI Advisory Platform in-app assistant. Answer using the provided application context. "
        "Use a calm Claude-style tone: short paragraphs, no bulky report dumps, and no long lists unless the user asks for details. "
        "Be concise, practical, and explicit when data is missing. Never claim you ran a workflow or changed data. "
        "For action requests, explain the next UI step and say confirmation is required before any action. "
        f"Access rule: {role_rules.get(role, 'Use only the supplied context.')}"
    )
    client = AsyncOpenAI(api_key=api_key)
    response = await client.chat.completions.create(
        model=config.get("openai_model_name", "gpt-4o-mini"),
        temperature=0.2,
        max_tokens=700,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": f"Application context JSON:\n{json.dumps(app_context, default=str)[:12000]}"},
            *messages[-8:],
        ],
    )
    return response.choices[0].message.content or "I could not generate an answer."


def render_ai_assistant(
    current_df: pd.DataFrame,
    comparison_df: pd.DataFrame,
    vuln_df: pd.DataFrame,
    readiness_df: pd.DataFrame,
    qa_df: pd.DataFrame,
    metrics_df: pd.DataFrame,
    *,
    render_context_selector: Callable[[str], None],
    run_async: Callable[[Any], Any],
) -> None:
    inject_assistant_css()
    title = assistant_page_name()
    role = current_role()
    app_context = build_assistant_context(current_df, comparison_df, vuln_df, readiness_df, qa_df, metrics_df)
    release_context = context_from_app(app_context)
    qa_summary = _qa_summary(qa_df)
    signoff = _load_signoff_for_context(active_team_name(), active_release_line() or str(app_context.get("release") or ""))
    st.markdown(
        f"""
        <div class="vm-claude-page">
            <div class="vm-claude-topbar">
                <h2 class="vm-claude-title">{active_team_name() or "Enterprise Product Release AI Advisory Platform"} {title}<span>v</span></h2>
                <div class="vm-claude-badges">
                    <span class="vm-claude-badge">Read-only</span>
                    <span class="vm-claude-badge">{role}</span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("<div class='vm-claude-context-wrap'>", unsafe_allow_html=True)
    render_context_selector("assistant")
    st.markdown("</div>", unsafe_allow_html=True)

    history_key = f"assistant_chat_{current_user().get('username', 'user')}_{role}"
    st.session_state.setdefault(history_key, [])
    st.markdown(
        f"""
        <div class="vm-claude-thread">
        """,
        unsafe_allow_html=True,
    )
    st.markdown("<div class='vm-claude-actions'>", unsafe_allow_html=True)
    if st.button("Clear Chat", use_container_width=False):
        st.session_state[history_key] = []
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    if not st.session_state[history_key]:
        empty_state = _assistant_empty_state_copy(role)
        suggestions = "".join(
            f'<span class="vm-claude-suggestion">{escape(suggestion)}</span>'
            for suggestion in empty_state["suggestions"]
        )
        st.markdown(
            f"""
            <div class="vm-claude-empty">
                <strong>Ask about the current release in plain language.</strong><br>
                {escape(empty_state["description"])}
                <div class="vm-claude-suggestions">
                    {suggestions}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    for message in st.session_state[history_key]:
        _render_message(
            message["role"],
            message["content"],
            qa_summary,
            app_context,
            signoff,
            str(message.get("widget") or ""),
            str(message.get("source") or ""),
        )
    st.markdown("</div>", unsafe_allow_html=True)

    prompt = st.chat_input(f"Ask {title} a question")
    if not prompt:
        return

    st.session_state[history_key].append({"role": "user", "content": prompt})
    tool_answer = _tool_first_answer(prompt, qa_df)
    if tool_answer:
        answer = tool_answer["content"]
        widget = tool_answer.get("widget", "")
        source = tool_answer.get("source", "Used MCP tool")
    else:
        current_mcp_answer, current_mcp_reason = run_async(_current_version_mcp_answer(prompt))
        if current_mcp_answer:
            answer = current_mcp_answer["content"]
            widget = current_mcp_answer.get("widget", "")
            source = current_mcp_answer.get("source", "Used MCP tool")
        else:
            latest_research_answer, latest_research_reason = run_async(_latest_version_research_answer(prompt))
            if latest_research_answer:
                answer = latest_research_answer["content"]
                widget = latest_research_answer.get("widget", "")
                source = latest_research_answer.get("source", "Used MCP tool")
            else:
                web_allowed, web_guardrail_reason = _web_search_allowed(prompt)
                web_answer, web_unavailable_reason = _web_search_answer(prompt) if web_allowed else (None, "")
                if web_answer:
                    answer = web_answer["content"]
                    widget = web_answer.get("widget", "")
                    source = web_answer.get("source", "Used web search")
                elif not web_allowed:
                    answer = web_guardrail_reason
                    widget = ""
                    source = "Access guardrail: Web search"
                else:
                    with st.spinner("Thinking..."):
                        try:
                            answer = run_async(ask_assistant(st.session_state[history_key], app_context))
                            fallback_reasons = "\n".join(
                                reason for reason in (current_mcp_reason, latest_research_reason, web_unavailable_reason) if reason
                            )
                            if fallback_reasons:
                                answer = f"{fallback_reasons}\n\n{answer}"
                        except Exception as exc:
                            logger.error("Assistant chat failed: %s", exc)
                            answer = f"The assistant could not answer right now: {exc}"
                    widget = ""
                    source = "Used AI fallback"
    answer, source, governance_verification = apply_final_governance(
        prompt=prompt,
        content=answer,
        source=source,
        role=role,
        team=active_team_name(),
        release=active_release_line(),
    )
    logger.info(
        "Assistant governance route user=%s role=%s team=%s release=%s source=%s passed=%s warnings=%s",
        current_user().get("username", "user"),
        role,
        active_team_name(),
        active_release_line(),
        source,
        governance_verification.passed,
        governance_verification.warnings,
    )
    st.session_state[history_key].append(
        {
            "role": "assistant",
            "content": answer,
            "widget": widget,
            "source": source,
        }
    )
    st.rerun()
