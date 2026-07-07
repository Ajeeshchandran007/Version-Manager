from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from App.agent_tools import build_streamlit_agent_tools
from App.auth import current_role, current_user
from App.emailing import prepare_email_report_files, qa_report_attachments
from App.workspace import (
    BASE_DIR,
    active_config,
    active_output_path,
    active_release_line,
    active_team_name,
    config_path_for_result,
    scoped_config_for_context,
    team_workspace_output_dir,
)
from App.workflow_locks import WorkflowAlreadyRunning, workflow_lock
from App.workflow_runs import record_workflow_run
from Core.compatibility_fetcher import CompatibilityRequirementFetcher
from Core.comparator import compare
from Core.notifier import count_actionable_updates, get_last_email_error, is_actionable_update, send_email
from Core.pdf_reader import PDFReader
from Core.server_querier import ServerQuerier
from Core.version_fetcher import VersionFetcher
from Core.vulnerability_checker import VulnerabilityChecker
from Utils.software_loader import load_software
from Utils.utils import load_config
from agent.context import build_release_context
from agent.multi_agent import LangGraphVersionManager
from mcp_server import _load_json, _resolve_current_version, _run_pipeline, _save_json, _vulnerability_path


def value(record: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return default


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
    user = current_user()
    release_context = build_release_context(
        team=team or active_team_name(),
        release=release or active_release_line(team or active_team_name()),
        role=current_role(),
        user=str(user.get("username") or user.get("display_name") or ""),
        category=config.get("default_category", "ALL"),
        output_dir=team_workspace_output_dir(team or active_team_name(), release or active_release_line(team or active_team_name())),
    )
    return {
        "config": config,
        "scoped_config": team is not None and release is not None,
        "release_context": release_context,
        "version_fetcher": VersionFetcher(),
        "pdf_reader": PDFReader(config),
        "server_querier": ServerQuerier(
            config,
            team=team or active_team_name(),
            release_line=release or active_release_line(team or active_team_name()),
        ),
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


def record_streamlit_workflow_run(
    *,
    run_id: str,
    team: str,
    release: str,
    workflow_scope: str,
    category: str,
    status: str,
    started_at: datetime,
    started_clock: float,
    summary: dict[str, Any],
    error_message: str = "",
) -> None:
    user = current_user()
    record_workflow_run(
        app_state_db_path(team, release),
        run_id=run_id,
        team=team,
        release_line=release,
        workflow_scope=workflow_scope,
        category=category,
        status=status,
        triggered_by=str(user.get("username") or ""),
        triggered_by_role=current_role(),
        started_at=started_at.isoformat(timespec="seconds"),
        ended_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        duration_seconds=round(time.perf_counter() - started_clock, 3),
        total=int(summary.get("total") or 0),
        needs_update_count=len(summary.get("needs_update") or []),
        unknown_count=len(summary.get("unknown") or []),
        email_sent=bool(summary.get("email_sent")),
        error_message=error_message or str(summary.get("error") or ""),
        summary=summary,
    )


async def trigger_full_pipeline(
    category: str,
    force_refresh: bool,
    team: str | None = None,
    release: str | None = None,
) -> dict[str, Any]:
    workflow_team = team or active_team_name()
    workflow_release = release or active_release_line(workflow_team)
    started_at = datetime.now().astimezone()
    started_clock = time.perf_counter()
    run_id = f"streamlit-full-{workflow_team}-{workflow_release}-{int(started_clock * 1000)}"
    try:
        with workflow_lock(
            app_state_db_path(workflow_team, workflow_release),
            team=workflow_team,
            release=workflow_release,
            scope="workflow",
            owner=workflow_owner(),
        ):
            state = runtime_state(workflow_team, workflow_release)
            workflow = LangGraphVersionManager(
                build_streamlit_agent_tools(state, resolve_vendor_compatibility_requirements),
                release_context=state.get("release_context"),
            )
            final_state = await workflow.run(
                "Run the full software version, security, package readiness, compatibility, QA validation, and reporting workflow.",
                category=category,
                force_refresh=force_refresh,
            )
    except WorkflowAlreadyRunning as exc:
        return {"error": str(exc), "operation": "full_pipeline"}
    except Exception as exc:
        summary = {"error": str(exc), "operation": "full_pipeline"}
        record_streamlit_workflow_run(
            run_id=run_id,
            team=workflow_team,
            release=workflow_release,
            workflow_scope="full",
            category=category,
            status="failed",
            started_at=started_at,
            started_clock=started_clock,
            summary=summary,
            error_message=str(exc),
        )
        return summary
    comparison = final_state.get("comparison_results", {})
    vulnerabilities = final_state.get("vulnerability_results", {})
    report_package = final_state.get("report_package", {})
    notification = report_package.get("notification", {})
    excel = report_package.get("excel", {})
    updates = [name for name, result in comparison.items() if is_actionable_update(result)]
    result = {
        "operation": "full_pipeline",
        "workflow": "LangGraph Supervisor",
        **active_workflow_context(workflow_team, workflow_release),
        "workflow_status": final_state.get("workflow_status"),
        "workflow_plan": final_state.get("workflow_plan", {}),
        "verification_result": final_state.get("verification_result", {}),
        "verification_retries": final_state.get("verification_retries", {}),
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
    record_streamlit_workflow_run(
        run_id=run_id,
        team=workflow_team,
        release=workflow_release,
        workflow_scope="full",
        category=category,
        status="completed",
        started_at=started_at,
        started_clock=started_clock,
        summary=result,
    )
    return result


async def trigger_scoped_pipeline(
    category: str,
    force_refresh: bool,
    workflow_scope: str,
    team: str | None = None,
    release: str | None = None,
) -> dict[str, Any]:
    workflow_team = team or active_team_name()
    workflow_release = release or active_release_line(workflow_team)
    started_at = datetime.now().astimezone()
    started_clock = time.perf_counter()
    try:
        with workflow_lock(
            app_state_db_path(workflow_team, workflow_release),
            team=workflow_team,
            release=workflow_release,
            scope="workflow",
            owner=workflow_owner(),
        ):
            state = runtime_state(workflow_team, workflow_release)
            state["triggered_by"] = workflow_owner()
            state["triggered_by_role"] = current_role()
            summary = await _run_pipeline(state, category, force_refresh=force_refresh, workflow_scope=workflow_scope)
    except WorkflowAlreadyRunning as exc:
        return {"error": str(exc), "operation": f"{workflow_scope}_workflow"}
    except Exception as exc:
        summary = {"error": str(exc), "operation": f"{workflow_scope}_workflow"}
        record_streamlit_workflow_run(
            run_id=f"streamlit-{workflow_scope}-{workflow_team}-{workflow_release}-{int(started_clock * 1000)}",
            team=workflow_team,
            release=workflow_release,
            workflow_scope=workflow_scope,
            category=category,
            status="failed",
            started_at=started_at,
            started_clock=started_clock,
            summary=summary,
            error_message=str(exc),
        )
        return summary
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
