from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pandas as pd
import streamlit as st
from openai import AsyncOpenAI

from App.auth import ROLE_ADMIN, ROLE_QA_ENGINEER, ROLE_RELEASE_ENGINEER, current_role, current_user
from App.workspace import active_release_line, active_team_name
from Utils.utils import load_config, logger


ROLE_ASSISTANT_PAGES = {
    ROLE_ADMIN: "AI Assistant",
    ROLE_RELEASE_ENGINEER: "Release Assistant",
    ROLE_QA_ENGINEER: "QA Assistant",
}


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
            ["Software Name", "Installation Status", "Test Result", "Test Cases Passed", "Test Cases Failed", "Test Notes"],
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
    title = assistant_page_name()
    role = current_role()
    subtitle = {
        ROLE_ADMIN: "Ask about operational posture, workflows, releases, QA, vulnerabilities, and reports.",
        ROLE_RELEASE_ENGINEER: "Ask about release readiness, package blockers, version drift, vulnerabilities, and reports.",
        ROLE_QA_ENGINEER: "Ask about QA validation, test impact, signoff readiness, version status, and reports.",
    }.get(role, "Ask questions about the current product version.")
    st.markdown(f"<div class='vm-section-title'><h2>{title}</h2><p>{subtitle}</p></div>", unsafe_allow_html=True)
    render_context_selector("assistant")
    st.caption(f"Context: {active_team_name()} / {active_release_line()} / {role}")

    history_key = f"assistant_chat_{current_user().get('username', 'user')}_{role}"
    st.session_state.setdefault(history_key, [])
    if st.button("Clear Chat", use_container_width=False):
        st.session_state[history_key] = []
        st.rerun()

    for message in st.session_state[history_key]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    prompt = st.chat_input(f"Ask {title} a question")
    if not prompt:
        return

    st.session_state[history_key].append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    app_context = build_assistant_context(current_df, comparison_df, vuln_df, readiness_df, qa_df, metrics_df)
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                answer = run_async(ask_assistant(st.session_state[history_key], app_context))
            except Exception as exc:
                logger.error("Assistant chat failed: %s", exc)
                answer = f"The assistant could not answer right now: {exc}"
        st.markdown(answer)
    st.session_state[history_key].append({"role": "assistant", "content": answer})
