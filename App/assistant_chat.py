from __future__ import annotations

import json
from html import escape
from collections.abc import Callable
from typing import Any

import pandas as pd
import streamlit as st
from openai import AsyncOpenAI

from App.auth import ROLE_ADMIN, ROLE_QA_ENGINEER, ROLE_RELEASE_ENGINEER, current_role, current_user
from App.qa_signoff import load_qa_signoff
from App.workspace import WORKSPACES_DIR, active_output_path, active_release_line, active_team_name
from Utils.utils import load_config, logger


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
    return "test case" in prompt_lower and any(term in prompt_lower for term in ("recommend", "recommended", "how many", "count", "total"))


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
    if _tested_by_requested(prompt):
        return {
            "content": _answer_tested_by(prompt, qa_df),
            "source": "Used app tool: QA Tester Details",
            "widget": "",
        }
    if _current_release_requested(prompt):
        return {
            "content": _answer_current_release(prompt),
            "source": "Used app tool: Release Context",
            "widget": "",
        }
    if _recommended_testcase_requested(prompt):
        return {
            "content": _answer_recommended_testcases(prompt),
            "source": "Used app tool: Test Case Impact",
            "widget": "",
        }
    if _qa_widget_requested(prompt):
        return {
            "content": "I prepared a compact QA dashboard snapshot from the current release data.",
            "source": "Used app tool: QA Validation",
            "widget": "qa_dashboard",
        }
    return None


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
    label_text = escape(source or "Version Manager")
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
        "You are the Version Manager in-app assistant. Answer using the provided application context. "
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
    qa_summary = _qa_summary(qa_df)
    signoff = _load_signoff_for_context(active_team_name(), active_release_line() or str(app_context.get("release") or ""))
    st.markdown(
        f"""
        <div class="vm-claude-page">
            <div class="vm-claude-topbar">
                <h2 class="vm-claude-title">{active_team_name() or "Version Manager"} {title}<span>v</span></h2>
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
        st.markdown(
            """
            <div class="vm-claude-empty">
                <strong>Ask about the current release in plain language.</strong><br>
                The assistant can summarize QA readiness, version drift, impacted tests, blockers, and report status.
                <div class="vm-claude-suggestions">
                    <span class="vm-claude-suggestion">Show QA dashboard summary</span>
                    <span class="vm-claude-suggestion">What should QA focus on?</span>
                    <span class="vm-claude-suggestion">Is this ready for signoff?</span>
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
        source = tool_answer.get("source", "Used app data")
    else:
        with st.spinner("Thinking..."):
            try:
                answer = run_async(ask_assistant(st.session_state[history_key], app_context))
            except Exception as exc:
                logger.error("Assistant chat failed: %s", exc)
                answer = f"The assistant could not answer right now: {exc}"
        widget = ""
        source = "Used AI fallback"
    st.session_state[history_key].append(
        {
            "role": "assistant",
            "content": answer,
            "widget": widget,
            "source": source,
        }
    )
    st.rerun()
