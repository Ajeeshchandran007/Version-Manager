# mcp_server.py
"""
Single MCP Server — Software Version Manager
---------------------------------------------
Tools exposed:
  1. fetch_latest_versions(category)  — web search via Tavily + OpenAI
  2. fetch_current_versions(category) — PDF extract via pdfminer + OpenAI
  3. compare_versions()               — diff latest vs current JSON
  4. send_notification()              — email the comparison report
  5. run_full_pipeline(category)      — runs 1→2→3→4 in sequence

Scheduler (APScheduler) runs run_full_pipeline() automatically
based on the cron expression in config.json.

Claude Desktop calls any tool conversationally.
No main.py / custom client needed.
"""

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from mcp.server.fastmcp import FastMCP, Context

from Core.compatibility_fetcher import CompatibilityRequirementFetcher
from Core.version_fetcher import VersionFetcher
from Core.pdf_reader import PDFReader
from Core.security_policy import apply_security_policy
from Core.server_querier import ServerQuerier
from Core.comparator import compare
from Core.excel_reporter import generate_excel_report
from Core.notifier import build_html_report, build_report, count_actionable_updates, get_last_email_error, is_actionable_update, send_email
from Core.observability import emit_event, new_trace_id, observed_step
from Core.reliability import with_retries
from Core.testcase_impact import load_testcase_repository, save_testcase_impact_outputs
from Core.vulnerability_checker import VulnerabilityChecker, local_assessment
from Core.workspace_assessment import (
    build_compatibility_assessment,
    build_package_readiness,
    build_qa_validation,
    merge_existing_qa_updates,
    save_workspace_outputs,
)
from Utils.software_loader import load_software, load_software_metadata
from Utils.utils import config_mtime, logger, load_config
from agent.memory import get_run_history as read_run_history
from agent.memory import init_db, log_audit

# ---------------------------------------------------------------------------
# Path resolution — single source of truth for output_files paths
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def _resolve_path(path: str) -> str:
    """
    Resolves a path from config.json against _PROJECT_ROOT if it's relative.
    This MUST be used anywhere a config output_files path is read, written,
    or checked for existence — otherwise writers and readers can disagree
    about where a file lives whenever the process cwd != _PROJECT_ROOT.
    """
    if not os.path.isabs(path):
        path = os.path.join(_PROJECT_ROOT, path)
    return path


def _save_json(data: dict, path: str):
    path = _resolve_path(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _load_json(path: str) -> dict:
    path = _resolve_path(path)
    with open(path) as f:
        return json.load(f)


def _path_exists(path: str) -> bool:
    return os.path.exists(_resolve_path(path))


def _vulnerability_path(config: dict) -> str:
    return config["output_files"].get("vulnerability_report_json", "output/vulnerability_report.json")


def _excel_assessment_path(config: dict) -> str:
    return config["output_files"].get("excel_assessment", "output/Software_Version_Assessment.xlsx")


def _package_readiness_path(config: dict) -> str:
    return config["output_files"].get("package_readiness_json", "output/package_readiness.json")


def _qa_validation_path(config: dict) -> str:
    return config["output_files"].get("qa_validation_json", "output/qa_validation.json")


def _testcase_repository_path(config: dict) -> str:
    return config["input_files"].get("testcase_repository_xlsx", "Input/testcaseRepository.xlsx")


def _testcase_impact_path(config: dict) -> str:
    return config["output_files"].get("testcase_impact_json", "output/testcase_impact.json")


def _testcase_impact_excel_path(config: dict) -> str:
    return config["output_files"].get("testcase_impact_xlsx", "output/Test_Case_Impact_Assessment.xlsx")


def _file_info(path: str) -> dict[str, Any]:
    resolved = _resolve_path(path)
    exists = os.path.exists(resolved)
    info: dict[str, Any] = {
        "path": resolved,
        "exists": exists,
    }
    if exists:
        stat = os.stat(resolved)
        info.update({
            "size_bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        })
    return info


def _safe_load_json(path: str) -> dict:
    return _load_json(path) if _path_exists(path) else {}


def _count_by_field(records: dict[str, dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records.values():
        value = str(record.get(field) or "UNKNOWN").upper()
        counts[value] = counts.get(value, 0) + 1
    return counts


def _is_blocked_package(record: dict[str, Any]) -> bool:
    status = str(record.get("Package Readiness") or "").lower()
    blocker = str(record.get("Blocker") or "").strip()
    return bool(blocker) or "block" in status or "review" in status


def _active_release_context(config: dict) -> dict[str, str]:
    software_yml = _resolve_path(config["input_files"].get("software_yml", ""))
    parts = os.path.normpath(software_yml).split(os.sep)
    context = {
        "team": "",
        "release": "",
        "software_yml": software_yml,
    }
    if "teams" in parts:
        index = parts.index("teams")
        if len(parts) > index + 1:
            context["team"] = parts[index + 1]
    if "releases" in parts:
        index = parts.index("releases")
        if len(parts) > index + 1:
            context["release"] = parts[index + 1]
    return context


def _active_workspace_output_dir(config: dict) -> str | None:
    context = _active_release_context(config)
    team = context.get("team")
    release = context.get("release")
    if not team:
        return None
    if release:
        return _resolve_path(os.path.join("workspaces", team, "releases", release, "output"))
    return _resolve_path(os.path.join("workspaces", team, "output"))


def _active_output_path(config: dict, output_key: str, fallback_path: str) -> str:
    workspace_dir = _active_workspace_output_dir(config)
    filename = os.path.basename(config.get("output_files", {}).get(output_key, fallback_path))
    if workspace_dir:
        workspace_path = os.path.join(workspace_dir, filename)
        if os.path.exists(workspace_path):
            return workspace_path
    return _resolve_path(config.get("output_files", {}).get(output_key, fallback_path))


def _active_output_write_path(config: dict, output_key: str, fallback_path: str) -> str:
    workspace_dir = _active_workspace_output_dir(config)
    filename = os.path.basename(config.get("output_files", {}).get(output_key, fallback_path))
    if workspace_dir:
        return os.path.join(workspace_dir, filename)
    return _resolve_path(config.get("output_files", {}).get(output_key, fallback_path))


def _active_aux_output_path(config: dict, filename: str) -> str:
    workspace_dir = _active_workspace_output_dir(config)
    if workspace_dir:
        return os.path.join(workspace_dir, filename)
    return _resolve_path(os.path.join("output", filename))


def _assess_vulnerabilities(
    software_name: str,
    version: str | None,
    needs_update: bool = False,
) -> dict:
    return local_assessment(software_name, version, needs_update)


def _refresh_state_config(state: dict) -> dict:
    """Reload config.json into a long-running MCP state when the file changes."""
    if state.get("scoped_config"):
        return state["config"]
    current_mtime = config_mtime()
    if current_mtime and current_mtime != state.get("config_mtime"):
        state["config"] = load_config()
        state["config_mtime"] = current_mtime
        logger.info("MCP runtime config refreshed from config.json.")
    return state["config"]


def _apply_scheduler_config(scheduler: AsyncIOScheduler, state: dict) -> None:
    """Apply the current config schedule to the MCP background scheduler."""
    config = _refresh_state_config(state)
    cron_expr = config.get("schedule_cron")
    category = config.get("default_category", "ALL")

    if not cron_expr:
        if scheduler.get_job("version_pipeline"):
            scheduler.remove_job("version_pipeline")
            logger.info("MCP scheduler disabled because schedule_cron is empty.")
        state["active_schedule_cron"] = ""
        state["active_schedule_category"] = category
        return

    if (
        state.get("active_schedule_cron") == cron_expr
        and state.get("active_schedule_category") == category
        and scheduler.get_job("version_pipeline")
    ):
        return

    scheduler.add_job(
        _scheduled_pipeline,
        CronTrigger.from_crontab(cron_expr),
        args=[state, category],
        id="version_pipeline",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    state["active_schedule_cron"] = cron_expr
    state["active_schedule_category"] = category
    logger.info("MCP scheduler applied - cron='%s', category='%s'", cron_expr, category)


async def _refresh_scheduler_from_config(scheduler: AsyncIOScheduler, state: dict) -> None:
    """APScheduler monitor job: hot-apply config.json schedule edits."""
    try:
        _apply_scheduler_config(scheduler, state)
    except Exception as exc:
        logger.error("MCP scheduler config refresh failed: %s", exc, exc_info=True)


async def _build_vulnerability_report(
    state: dict,
    comparison: dict[str, dict],
    force_refresh: bool = False,
) -> dict[str, dict]:
    checker = state["vulnerability_checker"]
    report = {}
    for software, result in comparison.items():
        current = result.get("current") or {}
        latest = result.get("latest") or {}
        finding = await checker.check(
            software_name=software,
            version=current.get("Build Version"),
            needs_update=bool(result.get("needs_update")),
            force_refresh=force_refresh,
        )
        finding["version_assessed"] = "current"
        finding["current_version"] = current.get("Build Version")
        finding["current_cu"] = current.get("Cumulative Update (CU)")
        finding["latest_version"] = latest.get("Build Version")
        finding["latest_cu"] = latest.get("Cumulative Update (CU)")
        finding = apply_security_policy(
            finding,
            software_name=software,
            current=current,
            latest=latest,
            needs_update=bool(result.get("needs_update")),
        )
        report[software] = finding
    return report


# ---------------------------------------------------------------------------
# Lifespan — initialize all resources once when server starts
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(server: FastMCP):
    config = load_config()
    os.makedirs(_resolve_path("output"), exist_ok=True)
    init_db()

    state = {
        "config":          config,
        "version_fetcher": VersionFetcher(),
        "pdf_reader":      PDFReader(),
        "server_querier":  ServerQuerier(),
        "vulnerability_checker": VulnerabilityChecker(),
        "config_mtime": config_mtime(),
    }

    scheduler = AsyncIOScheduler()
    _apply_scheduler_config(scheduler, state)
    scheduler.add_job(
        _refresh_scheduler_from_config,
        "interval",
        seconds=15,
        args=[scheduler, state],
        id="config_hot_reload",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    cron_expr = state.get("active_schedule_cron", "")
    category = state.get("active_schedule_category", config.get("default_category", "ALL"))
    scheduler.start()
    logger.info(f"Scheduler started - cron='{cron_expr}', category='{category}'")

    yield state                          # <-- yield the dict as lifespan context

    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped. Server shutting down.")


async def _resolve_current_version(querier: ServerQuerier, reader: PDFReader, name: str) -> dict:
    """
    Strategy 1: query live server (SSH/HTTP).
    Strategy 2: fall back to PDF extraction if server unreachable/unconfigured.
    """
    result = await querier.fetch(name)
    if result and (result.get("Build Version") or result.get("Cumulative Update (CU)")):
        result.setdefault("source", "live server")
        return result

    logger.info(f"'{name}': live server unavailable/empty - falling back to PDF.")
    pdf_result = await reader.fetch(name)
    pdf_result["source"] = "PDF fallback - server unreachable"
    return pdf_result


# ---------------------------------------------------------------------------
# Internal pipeline function (used by scheduler AND workflow tools)
# ---------------------------------------------------------------------------
async def _run_pipeline(
    state: dict,
    category: str,
    force_refresh: bool = False,
    workflow_scope: str = "full",
) -> dict:
    """
    Executes the full pipeline:
        fetch latest → fetch current → compare → notify
    Returns a summary dict.
    """
    workflow_scope = (workflow_scope or "full").lower().strip()
    if workflow_scope not in {"shared", "package", "qa", "full"}:
        return {"error": f"Unsupported workflow_scope '{workflow_scope}'"}
    write_security_outputs = workflow_scope in {"package", "full"}
    write_package_outputs = workflow_scope in {"package", "full"}
    write_qa_outputs = workflow_scope in {"qa", "full"}

    config  = _refresh_state_config(state)
    fetcher = state["version_fetcher"]
    reader  = state["pdf_reader"]
    querier = state["server_querier"]
    trace_id = new_trace_id("pipeline")
    emit_event("pipeline.started", trace_id, category=category)

    yml_path = config["input_files"]["software_yml"]
    software_list = load_software(yml_path, category)
    software_metadata = load_software_metadata(yml_path, category)

    if not software_list:
        return {"error": f"No software found for category '{category}'"}

    logger.info(f"Pipeline started - {len(software_list)} items, category='{category}'")

    # Step 1 — Latest versions from web
    logger.info("Step 1: Fetching latest versions from web...")
    latest: dict[str, dict] = {}
    with observed_step("fetch_latest_versions", trace_id, category=category, total=len(software_list)):
        for name in software_list:
            latest[name] = await with_retries(
                lambda n=name: fetcher.fetch(n, force_refresh=force_refresh),
                operation_name=f"latest:{name}",
            )

    out = _active_output_write_path(config, "latest_version_json", "output/latest_versions.json")
    _save_json(latest, out)
    logger.info(f"Latest versions saved -> {_resolve_path(out)}")

    # Step 2 — Current versions: live server first, PDF fallback
    logger.info("Step 2: Resolving current versions (server -> PDF fallback)...")
    current: dict[str, dict] = {}
    scanned_at = datetime.now().astimezone().isoformat()
    with observed_step("fetch_current_versions", trace_id, category=category, total=len(software_list)):
        for name in software_list:
            record = await with_retries(
                lambda n=name: _resolve_current_version(querier, reader, n),
                operation_name=f"current:{name}",
            )
            if isinstance(record, dict):
                record["last_scanned"] = scanned_at
            current[name] = record

    out = _active_output_write_path(config, "current_version_json", "output/current_versions.json")
    _save_json(current, out)
    logger.info(f"Current versions saved -> {_resolve_path(out)}")

    # Step 3 — Compare
    logger.info("Step 3: Comparing versions...")
    with observed_step("compare_versions", trace_id, category=category, total=len(software_list)):
        report = compare(latest, current)

    out = _active_output_write_path(config, "comparison_report_json", "output/comparison_report.json")
    _save_json(report, out)
    logger.info(f"Comparison report saved -> {_resolve_path(out)}")

    vulnerabilities = {}
    vulnerability_path = None
    if write_security_outputs:
        logger.info("Step 4: Assessing vulnerabilities...")
        with observed_step("check_vulnerabilities", trace_id, category=category, total=len(software_list)):
            vulnerabilities = await _build_vulnerability_report(state, report, force_refresh=force_refresh)

        vulnerability_path = _active_output_write_path(config, "vulnerability_report_json", _vulnerability_path(config))
        _save_json(vulnerabilities, vulnerability_path)
        logger.info(f"Vulnerability report saved -> {_resolve_path(vulnerability_path)}")
    else:
        logger.info("Step 4: Skipping vulnerability report for %s workflow.", workflow_scope)

    package_readiness = {}
    qa_validation = {}
    testcase_impact = {}
    package_path = None
    qa_path = None
    testcase_path = None
    testcase_excel_path = None

    if write_package_outputs or write_qa_outputs:
        logger.info(f"Step 5: Generating {workflow_scope} workspace assessments...")
        with observed_step("assess_workspaces", trace_id, category=category, total=len(software_list)):
            vendor_requirements = await _resolve_vendor_compatibility_requirements(report, latest, force_refresh=force_refresh)
            package_readiness = build_package_readiness(report, latest, vulnerabilities)
            if write_package_outputs:
                package_path = _active_output_write_path(config, "package_readiness_json", _package_readiness_path(config))
                _save_json(package_readiness, package_path)
                logger.info(f"Package readiness saved -> {package_path}")
            if write_qa_outputs:
                qa_validation = build_qa_validation(report, package_readiness, software_metadata, vendor_requirements)
                qa_path = _active_output_write_path(config, "qa_validation_json", _qa_validation_path(config))
                qa_validation = merge_existing_qa_updates(qa_validation, qa_path)
                _save_json(qa_validation, qa_path)
                logger.info(f"QA validation saved -> {qa_path}")

    if write_qa_outputs:
        logger.info("Step 6: Mapping impacted QA test cases...")
        with observed_step("testcase_impact", trace_id, category=category, total=len(software_list)):
            testcase_path = _active_output_write_path(config, "testcase_impact_json", _testcase_impact_path(config))
            testcase_excel_path = _active_output_write_path(config, "testcase_impact_xlsx", _testcase_impact_excel_path(config))
            testcase_impact = save_testcase_impact_outputs(
                report,
                _resolve_path(_testcase_repository_path(config)),
                testcase_path,
                testcase_excel_path,
            )
        logger.info(f"Test case impact saved -> {testcase_path}")
        logger.info(f"Test case impact workbook saved -> {testcase_excel_path}")

    excel_path = None
    if write_security_outputs:
        logger.info("Step 7: Generating Excel assessment workbook...")
        excel_path = _active_output_write_path(config, "excel_assessment", _excel_assessment_path(config))
        generate_excel_report(report, vulnerabilities, excel_path)
        logger.info(f"Excel assessment saved -> {excel_path}")

    logger.info("Step 8: Sending notification email...")
    body = build_report(report, vulnerabilities)
    html_body = build_html_report(report, vulnerabilities)
    updates = [n for n, v in report.items() if is_actionable_update(v)]
    actionable_updates = count_actionable_updates(report, vulnerabilities)
    unknown = [n for n, v in report.items() if v.get("unknown")]
    subject = (
        f"⚠️ {actionable_updates} software update(s) needed"
        if actionable_updates else (
            f"⚠️ {len(unknown)} software version status unknown"
            if unknown else "✅ All software versions are up to date"
        )
    )
    with observed_step("send_notification", trace_id, category=category):
        email_sent = send_email(subject, body, html_body=html_body)
    email_error = get_last_email_error()

    summary = {
        "category":      category,
        "workflow_scope": workflow_scope,
        "trace_id":      trace_id,
        "cache_mode":    "fresh" if force_refresh else "use_cache",
        "total":         len(software_list),
        "needs_update":  updates,
        "unknown":       unknown,
        "vulnerability_report": _resolve_path(vulnerability_path) if vulnerability_path else None,
        "package_readiness_report": _resolve_path(package_path) if package_path else None,
        "qa_validation_report": _resolve_path(qa_path) if qa_path else None,
        "testcase_impact_report": _resolve_path(testcase_path) if testcase_path else None,
        "testcase_impact_excel": _resolve_path(testcase_excel_path) if testcase_excel_path else None,
        "excel_assessment": excel_path,
        "vulnerabilities": vulnerabilities,
        "package_readiness": package_readiness,
        "qa_validation": qa_validation,
        "testcase_impact": testcase_impact,
        "email_sent":    email_sent,
        "email_error":   email_error,
    }
    logger.info(f"Pipeline complete: {summary}")
    emit_event("pipeline.completed", **summary)
    return summary


async def _scheduled_pipeline(state: dict, category: str):
    """Wrapper called by APScheduler (no Context available here)."""
    logger.info("Scheduled pipeline triggered.")
    try:
        summary = await _run_pipeline(state, category)
        logger.info(f"Scheduled pipeline finished: {summary}")
    except Exception as e:
        logger.error(f"Scheduled pipeline error: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
mcp = FastMCP("VersionManagerServer", lifespan=lifespan)


def _json_response(data: dict | list) -> str:
    return json.dumps(data, indent=2)


async def _resolve_vendor_compatibility_requirements(
    comparison: dict,
    latest: dict,
    force_refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    fetcher = CompatibilityRequirementFetcher()
    requirements: dict[str, dict[str, Any]] = {}
    for name, record in comparison.items():
        target = record.get("latest", {}) if isinstance(record, dict) else {}
        latest_version = target.get("Build Version") or target.get("version") or ""
        latest_record = latest.get(name, {}) if isinstance(latest, dict) else {}
        source_url = latest_record.get("Release Notes") or latest_record.get("source_url") or ""
        extracted = await fetcher.fetch(name, str(latest_version or ""), str(source_url or ""), force_refresh=force_refresh)
        if extracted:
            requirements[name] = extracted
    return requirements


@mcp.tool()
async def get_software_list(ctx: Context, category: str = "ALL") -> str:
    """Returns configured software names for a category."""
    state = ctx.request_context.lifespan_context
    config = _refresh_state_config(state)
    software = load_software(config["input_files"]["software_yml"], category)
    return _json_response({"category": category, "software": software})


@mcp.tool()
async def query_server(ctx: Context, software_name: str) -> str:
    """Queries the configured target server for one installed software version."""
    state = ctx.request_context.lifespan_context
    result = await state["server_querier"].fetch(software_name)
    if result:
        result.setdefault("source", "live server")
    return _json_response(result or {"source": "live server", "error": "No version returned"})


@mcp.tool()
async def extract_from_pdf(ctx: Context, software_name: str) -> str:
    """Extracts one installed software version from the configured PDF."""
    state = ctx.request_context.lifespan_context
    result = await state["pdf_reader"].fetch(software_name)
    result.setdefault("source", "PDF fallback")
    return _json_response(result)


@mcp.tool()
async def search_latest_version(ctx: Context, software_name: str, force_refresh: bool = False) -> str:
    """Searches vendor/web sources for the latest version of one software item."""
    state = ctx.request_context.lifespan_context
    result = await state["version_fetcher"].fetch(software_name, force_refresh=force_refresh)
    return _json_response(result)


# ---------------------------------------------------------------------------
# Tool 1 — Fetch Latest Versions
# ---------------------------------------------------------------------------
@mcp.tool()
async def fetch_latest_versions(ctx: Context, category: str = "ALL", force_refresh: bool = False) -> str:
    """
    Searches the web for the latest build version and CU for each software
    in the given category (SourceOne, DPS, Other, ALL).
    Results are saved to output/latest_versions.json.
    """
    state    = ctx.request_context.lifespan_context  
    config   = _refresh_state_config(state)
    fetcher  = state["version_fetcher"]
    yml_path = config["input_files"]["software_yml"]

    software_list = load_software(yml_path, category)
    if not software_list:
        return f"No software found for category '{category}'."

    await ctx.info(f"Fetching latest versions for {len(software_list)} items...")

    latest: dict[str, dict] = {}
    for i, name in enumerate(software_list):
        await ctx.report_progress(i, len(software_list))
        await ctx.info(f"Searching: {name}")
        latest[name] = await fetcher.fetch(name, force_refresh=force_refresh)

    out = _active_output_write_path(config, "latest_version_json", "output/latest_versions.json")
    _save_json(latest, out)

    updates = {k: v for k, v in latest.items() if v.get("Build Version")}
    missing = [name for name, result in latest.items() if not result.get("Build Version")]
    found_lines = [
        f"- {name}: {result.get('Build Version')}"
        + (f" ({result.get('Cumulative Update (CU)')})" if result.get("Cumulative Update (CU)") else "")
        for name, result in updates.items()
    ]
    missing_lines = [f"- {name}: Not Found" for name in missing]
    return (
        f"Latest versions fetched for {len(software_list)} software items.\n"
        f"Cache mode: {'fresh' if force_refresh else 'use_cache'}.\n"
        f"Found version info for {len(updates)} item(s):\n"
        f"{chr(10).join(found_lines) if found_lines else '- none'}\n"
        f"Version not found for {len(missing)} item(s):\n"
        f"{chr(10).join(missing_lines) if missing_lines else '- none'}\n"
        f"Saved to: {_resolve_path(out)}"
    )


# ---------------------------------------------------------------------------
# Tool 2 — Fetch Current Versions
# ---------------------------------------------------------------------------
@mcp.tool()
async def fetch_current_versions(ctx: Context, category: str = "ALL") -> str:
    """
    Determines the currently installed build version and CU for each software
    in the given category.

    Strategy per software:
      1. Query the live server (SSH command or HTTP API, per config.json "servers").
      2. If the server is unreachable, not configured, or returns no version
         info, fall back to extracting from the PDF document.

    Results (including the data source used) are saved to output/current_versions.json.
    """
    state    = ctx.request_context.lifespan_context
    config   = _refresh_state_config(state)
    reader   = state["pdf_reader"]
    querier  = state["server_querier"]
    yml_path = config["input_files"]["software_yml"]

    software_list = load_software(yml_path, category)
    if not software_list:
        return f"No software found for category '{category}'."

    await ctx.info(f"Resolving current versions for {len(software_list)} items (server-first, PDF fallback)...")

    current: dict[str, dict] = {}
    scanned_at = datetime.now().astimezone().isoformat()
    for i, name in enumerate(software_list):
        await ctx.report_progress(i, len(software_list))
        await ctx.info(f"Resolving: {name}")
        record = await _resolve_current_version(querier, reader, name)
        if isinstance(record, dict):
            record["last_scanned"] = scanned_at
        current[name] = record

    out = _active_output_write_path(config, "current_version_json", "output/current_versions.json")
    _save_json(current, out)

    from_server = [n for n, v in current.items() if v.get("source") == "live server"]
    from_pdf    = [n for n in current if n not in from_server]

    return (
        f"Current versions resolved for {len(software_list)} software items.\n"
        f"From live server ({len(from_server)}): {', '.join(from_server) or 'none'}\n"
        f"From PDF fallback ({len(from_pdf)}): {', '.join(from_pdf) or 'none'}\n"
        f"Saved to: {_resolve_path(out)}"
    )


# ---------------------------------------------------------------------------
# Tool 3 — Compare Versions
# ---------------------------------------------------------------------------
@mcp.tool()
async def compare_versions(ctx: Context, latest: dict | None = None, current: dict | None = None) -> str:
    """
    Compares the latest versions (from web) against current versions (from PDF).
    Reads output/latest_versions.json and output/current_versions.json.
    Saves the comparison report to output/comparison_report.json.
    """
    state  = ctx.request_context.lifespan_context
    config = _refresh_state_config(state)

    latest_path = _active_output_path(config, "latest_version_json", config["output_files"]["latest_version_json"])
    current_path = _active_output_path(config, "current_version_json", config["output_files"]["current_version_json"])

    if latest is None or current is None:
        # IMPORTANT: check existence via the same resolution logic used to save,
        # otherwise a relative path can resolve differently here than it did
        # when fetch_latest_versions/fetch_current_versions wrote the file.
        if not _path_exists(latest_path):
            return f"Latest versions file not found at {_resolve_path(latest_path)}. Run fetch_latest_versions first."
        if not _path_exists(current_path):
            return f"Current versions file not found at {_resolve_path(current_path)}. Run fetch_current_versions first."

        latest  = _load_json(latest_path)
        current = _load_json(current_path)

    await ctx.info("Comparing versions...")
    report = compare(latest, current)

    out = _active_output_write_path(config, "comparison_report_json", "output/comparison_report.json")
    _save_json(report, out)

    return _json_response(report)


@mcp.tool()
async def get_run_history(ctx: Context, software_name: str, limit: int = 5) -> str:
    """Returns recent comparison history for one software item."""
    return _json_response({"software_name": software_name, "history": read_run_history(software_name, limit)})


@mcp.tool()
async def check_vulnerabilities(
    ctx: Context,
    software_name: str,
    version: str | None = None,
    needs_update: bool = False,
    force_refresh: bool = False,
) -> str:
    """
    Performs a vulnerability assessment boundary for one software item.

    Uses the NVD CVE API when available, with local assessment fallback.
    """
    state = ctx.request_context.lifespan_context
    result = await state["vulnerability_checker"].check(
        software_name,
        version,
        needs_update,
        force_refresh=force_refresh,
    )
    return _json_response(result)


@mcp.tool()
async def save_vulnerability_report(ctx: Context, vulnerabilities: dict) -> str:
    """Saves aggregate vulnerability findings to output/vulnerability_report.json."""
    state = ctx.request_context.lifespan_context
    config = _refresh_state_config(state)
    out = _active_output_write_path(config, "vulnerability_report_json", _vulnerability_path(config))
    _save_json(vulnerabilities, out)
    return _json_response({
        "saved": True,
        "path": _resolve_path(out),
        "total": len(vulnerabilities),
    })


@mcp.tool()
async def generate_excel_assessment(ctx: Context) -> str:
    """Generates output/Software_Version_Assessment.xlsx from saved reports."""
    state = ctx.request_context.lifespan_context
    config = _refresh_state_config(state)
    comparison_path = _active_output_path(config, "comparison_report_json", config["output_files"]["comparison_report_json"])
    vulnerability_path = _active_output_path(config, "vulnerability_report_json", _vulnerability_path(config))

    if not _path_exists(comparison_path):
        return f"Comparison report not found at {_resolve_path(comparison_path)}. Run compare_versions first."
    if not _path_exists(vulnerability_path):
        return f"Vulnerability report not found at {_resolve_path(vulnerability_path)}. Run check_vulnerabilities first."

    excel_path = _active_output_write_path(config, "excel_assessment", _excel_assessment_path(config))
    generate_excel_report(_load_json(comparison_path), _load_json(vulnerability_path), excel_path)
    return _json_response({"saved": True, "path": excel_path})


# ---------------------------------------------------------------------------
# Tool 4 — Send Notification
# ---------------------------------------------------------------------------
@mcp.tool()
async def assess_package_readiness(
    ctx: Context,
    comparison: dict | None = None,
    latest: dict | None = None,
    vulnerabilities: dict | None = None,
) -> str:
    """Generates package readiness from supplied state or saved workflow reports."""
    state = ctx.request_context.lifespan_context
    config = _refresh_state_config(state)
    comparison_path = _active_output_path(config, "comparison_report_json", config["output_files"]["comparison_report_json"])
    latest_path = _active_output_path(config, "latest_version_json", config["output_files"]["latest_version_json"])
    vulnerability_path = _active_output_path(config, "vulnerability_report_json", _vulnerability_path(config))

    if comparison is None and not _path_exists(comparison_path):
        return f"Comparison report not found at {_resolve_path(comparison_path)}. Run compare_versions first."
    if latest is None and not _path_exists(latest_path):
        return f"Latest versions file not found at {_resolve_path(latest_path)}. Run fetch_latest_versions first."

    comparison = comparison if comparison is not None else _load_json(comparison_path)
    latest = latest if latest is not None else _load_json(latest_path)
    vulnerabilities = vulnerabilities if vulnerabilities is not None else (
        _load_json(vulnerability_path) if _path_exists(vulnerability_path) else {}
    )
    readiness = build_package_readiness(comparison, latest, vulnerabilities)
    out = _active_output_write_path(config, "package_readiness_json", _package_readiness_path(config))
    _save_json(readiness, out)
    return _json_response({
        "saved": True,
        "path": _resolve_path(out),
        "total": len(readiness),
        "package_readiness": readiness,
    })


@mcp.tool()
async def get_active_config(ctx: Context, category: str | None = None) -> str:
    """Returns the active VersionManager config paths and selected software list."""
    state = ctx.request_context.lifespan_context
    config = _refresh_state_config(state)
    selected_category = category or config.get("default_category", "ALL")
    release_context = _active_release_context(config)
    software_yml = config["input_files"]["software_yml"]
    software = load_software(software_yml, selected_category)
    paths = {
        "software_yml": _file_info(software_yml),
        "current_version_pdf": _file_info(config["input_files"].get("current_version_pdf", "")),
        "testcase_repository_xlsx": _file_info(_testcase_repository_path(config)),
        "output_directory": _active_workspace_output_dir(config) or _resolve_path("output"),
    }
    return _json_response({
        "category": selected_category,
        "active_team": release_context["team"],
        "active_release": release_context["release"],
        "project_root": _PROJECT_ROOT,
        "input_files": paths,
        "output_files": config.get("output_files", {}),
        "active_output_files": {
            name: _active_output_write_path(config, name, path)
            for name, path in config.get("output_files", {}).items()
        },
        "software_count": len(software),
        "software": software,
    })


@mcp.tool()
async def get_package_readiness_summary(ctx: Context) -> str:
    """Summarizes saved package readiness results without regenerating reports."""
    state = ctx.request_context.lifespan_context
    config = _refresh_state_config(state)
    path = _active_output_path(config, "package_readiness_json", _package_readiness_path(config))
    readiness = _safe_load_json(path)
    if not readiness:
        return _json_response({
            "available": False,
            "path": _resolve_path(path),
            "message": "Package readiness report not found. Run assess_package_readiness or run_full_pipeline first.",
        })
    statuses = _count_by_field(readiness, "Package Readiness")
    impact = _count_by_field(readiness, "Upgrade Impact")
    blocked = {name: item for name, item in readiness.items() if _is_blocked_package(item)}
    return _json_response({
        "available": True,
        "path": _resolve_path(path),
        "total": len(readiness),
        "status_counts": statuses,
        "impact_counts": impact,
        "blocked_count": len(blocked),
        "blocked_packages": list(blocked.keys()),
    })


@mcp.tool()
async def get_package_dashboard(ctx: Context) -> str:
    """Returns a package-team dashboard from saved readiness and comparison outputs."""
    state = ctx.request_context.lifespan_context
    config = _refresh_state_config(state)
    readiness_path = _active_output_path(config, "package_readiness_json", _package_readiness_path(config))
    comparison_path = _active_output_path(config, "comparison_report_json", config["output_files"]["comparison_report_json"])
    release_context = _active_release_context(config)
    readiness = _safe_load_json(readiness_path)
    comparison = _safe_load_json(comparison_path)
    actionable = [name for name, item in comparison.items() if is_actionable_update(item)]
    blocked = {name: item for name, item in readiness.items() if _is_blocked_package(item)}
    ready = [
        name for name, item in readiness.items()
        if not _is_blocked_package(item) and str(item.get("Package Readiness") or "").strip()
    ]
    return _json_response({
        "readiness_available": bool(readiness),
        "active_team": release_context["team"],
        "active_release": release_context["release"],
        "comparison_available": bool(comparison),
        "total_packages": len(readiness),
        "software_requiring_update": len(actionable),
        "ready_count": len(ready),
        "blocked_count": len(blocked),
        "status_counts": _count_by_field(readiness, "Package Readiness") if readiness else {},
        "blocked_packages": blocked,
        "paths": {
            "package_readiness": _resolve_path(readiness_path),
            "comparison_report": _resolve_path(comparison_path),
        },
    })


@mcp.tool()
async def get_blocked_packages(ctx: Context) -> str:
    """Returns package readiness items that are blocked or require review."""
    state = ctx.request_context.lifespan_context
    config = _refresh_state_config(state)
    path = _active_output_path(config, "package_readiness_json", _package_readiness_path(config))
    readiness = _safe_load_json(path)
    blocked = {
        name: {
            "Package Readiness": item.get("Package Readiness"),
            "Upgrade Impact": item.get("Upgrade Impact"),
            "Owner": item.get("Owner"),
            "Blocker": item.get("Blocker"),
            "Target Version": item.get("Target Version"),
        }
        for name, item in readiness.items()
        if _is_blocked_package(item)
    }
    return _json_response({
        "path": _resolve_path(path),
        "total": len(blocked),
        "blocked_packages": blocked,
    })


@mcp.tool()
async def get_package_checklist(ctx: Context, software_name: str | None = None) -> str:
    """Returns package checklist status from saved package readiness output."""
    state = ctx.request_context.lifespan_context
    config = _refresh_state_config(state)
    path = _active_output_path(config, "package_readiness_json", _package_readiness_path(config))
    readiness = _safe_load_json(path)
    if software_name:
        candidates = {software_name: readiness.get(software_name, {})}
    else:
        candidates = readiness
    checklist = {}
    for name, item in candidates.items():
        checks = item.get("Checklist") if isinstance(item, dict) else {}
        if not isinstance(checks, dict):
            checks = {}
        checklist[name] = {
            "Package Readiness": item.get("Package Readiness") if isinstance(item, dict) else None,
            "Checklist": checks,
            "completed": sum(1 for value in checks.values() if bool(value)),
            "total": len(checks),
            "pending": [key for key, value in checks.items() if not bool(value)],
        }
    return _json_response({
        "path": _resolve_path(path),
        "total": len(checklist),
        "checklist": checklist,
    })


@mcp.tool()
async def check_compatibility(
    ctx: Context,
    comparison: dict | None = None,
    package_readiness: dict | None = None,
) -> str:
    """Generates compatibility validation data from supplied state or saved reports."""
    state = ctx.request_context.lifespan_context
    config = _refresh_state_config(state)
    comparison_path = _active_output_path(config, "comparison_report_json", config["output_files"]["comparison_report_json"])
    latest_path = _active_output_path(config, "latest_version_json", config["output_files"]["latest_version_json"])
    package_path = _active_output_path(config, "package_readiness_json", _package_readiness_path(config))

    if comparison is None and not _path_exists(comparison_path):
        return f"Comparison report not found at {_resolve_path(comparison_path)}. Run compare_versions first."

    comparison = comparison if comparison is not None else _load_json(comparison_path)
    readiness = package_readiness if package_readiness is not None else (
        _load_json(package_path) if _path_exists(package_path) else {}
    )
    latest = _load_json(latest_path) if _path_exists(latest_path) else {}
    vendor_requirements = await _resolve_vendor_compatibility_requirements(comparison, latest)
    metadata = load_software_metadata(config["input_files"]["software_yml"], config.get("default_category", "ALL"))
    compatibility = build_compatibility_assessment(comparison, readiness, metadata, vendor_requirements)
    return _json_response({
        "saved": True,
        "total": len(compatibility),
        "compatibility": compatibility,
    })


@mcp.tool()
async def generate_qa_validation(
    ctx: Context,
    comparison: dict | None = None,
    package_readiness: dict | None = None,
) -> str:
    """Generates QA validation results from supplied state or saved workflow reports."""
    state = ctx.request_context.lifespan_context
    config = _refresh_state_config(state)
    comparison_path = _active_output_path(config, "comparison_report_json", config["output_files"]["comparison_report_json"])
    latest_path = _active_output_path(config, "latest_version_json", config["output_files"]["latest_version_json"])
    package_path = _active_output_path(config, "package_readiness_json", _package_readiness_path(config))

    if comparison is None and not _path_exists(comparison_path):
        return f"Comparison report not found at {_resolve_path(comparison_path)}. Run compare_versions first."

    comparison = comparison if comparison is not None else _load_json(comparison_path)
    readiness = package_readiness if package_readiness is not None else (
        _load_json(package_path) if _path_exists(package_path) else {}
    )
    latest = _load_json(latest_path) if _path_exists(latest_path) else {}
    vendor_requirements = await _resolve_vendor_compatibility_requirements(comparison, latest)
    metadata = load_software_metadata(config["input_files"]["software_yml"], config.get("default_category", "ALL"))
    qa_validation = build_qa_validation(comparison, readiness, metadata, vendor_requirements)
    out = _active_output_write_path(config, "qa_validation_json", _qa_validation_path(config))
    qa_validation = merge_existing_qa_updates(qa_validation, out)
    _save_json(qa_validation, out)
    return _json_response({
        "saved": True,
        "path": _resolve_path(out),
        "total": len(qa_validation),
        "qa_validation": qa_validation,
    })


@mcp.tool()
async def get_qa_dashboard(ctx: Context) -> str:
    """Returns a QA dashboard from saved QA validation and test case impact outputs."""
    state = ctx.request_context.lifespan_context
    config = _refresh_state_config(state)
    qa_path = _active_output_path(config, "qa_validation_json", _qa_validation_path(config))
    impact_path = _active_output_path(config, "testcase_impact_json", _testcase_impact_path(config))
    signoff_path = _active_aux_output_path(config, "qa_signoff.json")
    release_context = _active_release_context(config)
    qa_validation = _safe_load_json(qa_path)
    testcase_impact = _safe_load_json(impact_path)
    qa_signoff = _safe_load_json(signoff_path)
    results = _count_by_field(qa_validation, "Test Result") if qa_validation else {}
    install_status = _count_by_field(qa_validation, "Installation Status") if qa_validation else {}
    not_ready = {
        name: item for name, item in qa_validation.items()
        if str(item.get("Test Result") or "").upper() in {"FAIL", "FAILED", "WARNING", "BLOCKED", "NOT TESTED"}
    }
    return _json_response({
        "qa_validation_available": bool(qa_validation),
        "active_team": release_context["team"],
        "active_release": release_context["release"],
        "testcase_impact_available": bool(testcase_impact),
        "qa_signoff_available": bool(qa_signoff),
        "qa_signoff": qa_signoff,
        "total_software": len(qa_validation),
        "test_result_counts": results,
        "installation_status_counts": install_status,
        "not_ready_count": len(not_ready),
        "testcase_summary": testcase_impact.get("summary", {}),
        "paths": {
            "qa_validation": _resolve_path(qa_path),
            "testcase_impact": _resolve_path(impact_path),
            "testcase_impact_excel": _active_output_path(config, "testcase_impact_xlsx", _testcase_impact_excel_path(config)),
            "qa_signoff": signoff_path,
        },
    })


@mcp.tool()
async def get_testcase_coverage(ctx: Context, category: str | None = None) -> str:
    """Compares configured software against testcaseRepository.xlsx coverage."""
    state = ctx.request_context.lifespan_context
    config = _refresh_state_config(state)
    selected_category = category or config.get("default_category", "ALL")
    release_context = _active_release_context(config)
    software = load_software(config["input_files"]["software_yml"], selected_category)
    repo_path = _resolve_path(_testcase_repository_path(config))
    repository = load_testcase_repository(repo_path)
    coverage: dict[str, dict[str, Any]] = {}
    normalized_repo = (
        repository["Software Name"].astype(str).str.lower().str.strip()
        if not repository.empty and "Software Name" in repository.columns
        else []
    )
    for name in software:
        needle = name.lower().strip()
        if repository.empty:
            matches = repository
        else:
            exact = repository[normalized_repo == needle]
            if exact.empty:
                tokens = [token for token in needle.replace("-", " ").split() if len(token) >= 4]
                mask = normalized_repo.apply(lambda value: any(token in value for token in tokens))
                matches = repository[mask]
            else:
                matches = exact
        coverage[name] = {
            "covered": not matches.empty,
            "test_case_count": int(len(matches)),
            "test_case_ids": matches["Test Case ID"].astype(str).tolist() if not matches.empty else [],
            "owners": sorted(set(matches["Owner"].astype(str))) if not matches.empty else [],
        }
    uncovered = [name for name, item in coverage.items() if not item["covered"]]
    return _json_response({
        "category": selected_category,
        "active_team": release_context["team"],
        "active_release": release_context["release"],
        "repository_path": repo_path,
        "software_count": len(software),
        "covered_count": len(software) - len(uncovered),
        "uncovered_count": len(uncovered),
        "uncovered_software": uncovered,
        "coverage": coverage,
    })


@mcp.tool()
async def get_failed_qa_items(ctx: Context) -> str:
    """Returns QA validation items with failed, warning, blocked, or not-tested status."""
    state = ctx.request_context.lifespan_context
    config = _refresh_state_config(state)
    path = _active_output_path(config, "qa_validation_json", _qa_validation_path(config))
    qa_validation = _safe_load_json(path)
    failed_statuses = {"FAIL", "FAILED", "WARNING", "BLOCKED", "NOT TESTED"}
    failed = {
        name: {
            "Test Result": item.get("Test Result"),
            "Installation Status": item.get("Installation Status"),
            "Compatibility Status": item.get("Compatibility Status"),
            "Test Notes": item.get("Test Notes"),
            "Tested By": item.get("Tested By"),
            "Evidence File": item.get("Evidence File"),
        }
        for name, item in qa_validation.items()
        if str(item.get("Test Result") or "").upper() in failed_statuses
    }
    return _json_response({
        "path": _resolve_path(path),
        "total": len(failed),
        "failed_qa_items": failed,
    })


@mcp.tool()
async def get_qa_testers(ctx: Context) -> str:
    """Returns tester, result, and execution status for saved QA validation items."""
    state = ctx.request_context.lifespan_context
    config = _refresh_state_config(state)
    release_context = _active_release_context(config)
    path = _active_output_path(config, "qa_validation_json", _qa_validation_path(config))
    qa_validation = _safe_load_json(path)
    testers = {
        name: {
            "Tested By": item.get("Tested By"),
            "Test Date": item.get("Test Date"),
            "Test Result": item.get("Test Result"),
            "Installation Status": item.get("Installation Status"),
            "Test Case Count": item.get("Test Case Count"),
            "Test Cases Executed": item.get("Test Cases Executed"),
            "Test Notes": item.get("Test Notes"),
        }
        for name, item in qa_validation.items()
    }
    return _json_response({
        "active_team": release_context["team"],
        "active_release": release_context["release"],
        "path": _resolve_path(path),
        "total": len(testers),
        "qa_testers": testers,
    })


@mcp.tool()
async def save_package_readiness(ctx: Context, package_readiness: dict) -> str:
    """Saves package readiness results to output/package_readiness.json."""
    state = ctx.request_context.lifespan_context
    config = _refresh_state_config(state)
    out = _active_output_write_path(config, "package_readiness_json", _package_readiness_path(config))
    _save_json(package_readiness, out)
    return _json_response({"saved": True, "path": _resolve_path(out), "total": len(package_readiness)})


@mcp.tool()
async def save_qa_validation(ctx: Context, qa_validation: dict) -> str:
    """Saves QA validation results to output/qa_validation.json."""
    state = ctx.request_context.lifespan_context
    config = _refresh_state_config(state)
    out = _active_output_write_path(config, "qa_validation_json", _qa_validation_path(config))
    _save_json(qa_validation, out)
    return _json_response({"saved": True, "path": _resolve_path(out), "total": len(qa_validation)})


@mcp.tool()
async def generate_testcase_impact(ctx: Context, comparison: dict | None = None) -> str:
    """Maps software requiring updates to recommended QA test cases."""
    state = ctx.request_context.lifespan_context
    config = _refresh_state_config(state)
    comparison_path = _active_output_path(config, "comparison_report_json", config["output_files"]["comparison_report_json"])

    if comparison is None and not _path_exists(comparison_path):
        return f"Comparison report not found at {_resolve_path(comparison_path)}. Run compare_versions first."

    comparison = comparison if comparison is not None else _load_json(comparison_path)
    impact = save_testcase_impact_outputs(
        comparison,
        _resolve_path(_testcase_repository_path(config)),
        _active_output_write_path(config, "testcase_impact_json", _testcase_impact_path(config)),
        _active_output_write_path(config, "testcase_impact_xlsx", _testcase_impact_excel_path(config)),
    )
    return _json_response({
        "saved": True,
        "path": _active_output_write_path(config, "testcase_impact_json", _testcase_impact_path(config)),
        "excel_path": _active_output_write_path(config, "testcase_impact_xlsx", _testcase_impact_excel_path(config)),
        "summary": impact.get("summary", {}),
        "testcase_impact": impact,
    })


@mcp.tool()
async def get_output_files(ctx: Context) -> str:
    """Lists configured output files with existence, size, and modified time."""
    state = ctx.request_context.lifespan_context
    config = _refresh_state_config(state)
    files = {
        name: _file_info(_active_output_path(config, name, path))
        for name, path in config.get("output_files", {}).items()
    }
    signoff_path = _active_aux_output_path(config, "qa_signoff.json")
    files["qa_signoff_json"] = _file_info(signoff_path)
    existing = {name: info for name, info in files.items() if info["exists"]}
    return _json_response({
        "output_directory": _active_workspace_output_dir(config) or _resolve_path("output"),
        "total_configured": len(files),
        "total_existing": len(existing),
        "files": files,
    })


@mcp.tool()
async def get_release_artifacts(ctx: Context) -> str:
    """Returns release, package, and QA artifacts generated for the active workspace."""
    state = ctx.request_context.lifespan_context
    config = _refresh_state_config(state)
    release_context = _active_release_context(config)
    artifacts = {
        "latest_versions": _file_info(_active_output_path(config, "latest_version_json", "output/latest_versions.json")),
        "current_versions": _file_info(_active_output_path(config, "current_version_json", "output/current_versions.json")),
        "comparison_report": _file_info(_active_output_path(config, "comparison_report_json", "output/comparison_report.json")),
        "vulnerability_report": _file_info(_active_output_path(config, "vulnerability_report_json", _vulnerability_path(config))),
        "package_readiness": _file_info(_active_output_path(config, "package_readiness_json", _package_readiness_path(config))),
        "qa_validation": _file_info(_active_output_path(config, "qa_validation_json", _qa_validation_path(config))),
        "testcase_impact": _file_info(_active_output_path(config, "testcase_impact_json", _testcase_impact_path(config))),
        "testcase_impact_excel": _file_info(_active_output_path(config, "testcase_impact_xlsx", _testcase_impact_excel_path(config))),
        "excel_assessment": _file_info(_active_output_path(config, "excel_assessment", _excel_assessment_path(config))),
        "qa_signoff": _file_info(_active_aux_output_path(config, "qa_signoff.json")),
    }
    missing = [name for name, info in artifacts.items() if not info["exists"]]
    return _json_response({
        "project_root": _PROJECT_ROOT,
        "active_team": release_context["team"],
        "active_release": release_context["release"],
        "artifact_count": len(artifacts),
        "available_count": len(artifacts) - len(missing),
        "missing": missing,
        "artifacts": artifacts,
    })


@mcp.tool()
async def send_notification(ctx: Context, report: dict | None = None) -> str:
    """
    Reads the comparison report and sends an email notification.
    Run compare_versions first to generate the report.
    """
    state       = ctx.request_context.lifespan_context
    config      = _refresh_state_config(state)
    report_path = _active_output_path(config, "comparison_report_json", config["output_files"]["comparison_report_json"])
    vulnerability_path = _active_output_path(config, "vulnerability_report_json", _vulnerability_path(config))

    report_body = None
    if report and report.get("body"):
        report_body = report["body"]
        comparison = _load_json(report_path) if _path_exists(report_path) else {}
        vulnerabilities = _load_json(vulnerability_path) if _path_exists(vulnerability_path) else {}
    else:
        if not _path_exists(report_path):
            return f"Comparison report not found at {_resolve_path(report_path)}. Run compare_versions first."
        comparison = _load_json(report_path)
        vulnerabilities = _load_json(vulnerability_path) if _path_exists(vulnerability_path) else {}

    await ctx.info("Building report and sending email...")

    body = report_body or build_report(comparison, vulnerabilities)
    html_body = build_html_report(comparison, vulnerabilities)
    needs_update = [n for n, v in comparison.items() if is_actionable_update(v)]
    actionable_updates = count_actionable_updates(comparison, vulnerabilities)
    unknown      = [n for n, v in comparison.items() if v.get("unknown")]
    subject      = (
        f"⚠️ {actionable_updates} software update(s) needed"
        if actionable_updates else (
            f"⚠️ {len(unknown)} software version status unknown"
            if unknown else "✅ All software versions are up to date"
        )
    )

    sent = send_email(subject, body, html_body=html_body)
    error = get_last_email_error()
    return _json_response({
        "sent": sent,
        "subject": subject,
        "error": error,
    })


@mcp.tool()
async def log_audit_event(ctx: Context, step: str, details: dict | None = None) -> str:
    """Writes one audit record for the active multi-agent workflow."""
    run_id = (details or {}).get("run_id", "mcp")
    log_audit(run_id, step, "mcp", details or {})
    return _json_response({"logged": True, "run_id": run_id, "step": step})


def _format_pipeline_summary(summary: dict, title: str) -> str:
    updates = summary["needs_update"]
    unknown = summary.get("unknown", [])
    return (
        f"{title} complete for category='{summary.get('category')}'.\n"
        f"  Workflow scope         : {summary.get('workflow_scope')}\n"
        f"  Cache mode             : {summary.get('cache_mode')}\n"
        f"  Total software checked : {summary['total']}\n"
        f"  Needs update           : {', '.join(updates) or 'none'}\n"
        f"  Unknown                : {', '.join(unknown) or 'none'}\n"
        f"  Vulnerability report   : {summary.get('vulnerability_report')}\n"
        f"  Package readiness      : {summary.get('package_readiness_report') or 'not updated by this workflow'}\n"
        f"  QA validation          : {summary.get('qa_validation_report') or 'not updated by this workflow'}\n"
        f"  Test case impact       : {summary.get('testcase_impact_report') or 'not updated by this workflow'}\n"
        f"  Excel assessment       : {summary.get('excel_assessment')}\n"
        f"  Email sent             : {summary['email_sent']}"
    )


@mcp.tool()
async def run_shared_scan(ctx: Context, category: str = "ALL", force_refresh: bool = False) -> str:
    """Runs only shared scan outputs: latest, current, comparison, vulnerability, Excel, and email."""
    state = ctx.request_context.lifespan_context
    await ctx.info(f"Starting shared scan for category='{category}'...")
    summary = await _run_pipeline(state, category, force_refresh=force_refresh, workflow_scope="shared")
    if "error" in summary:
        return f"Shared scan failed: {summary['error']}"
    return _format_pipeline_summary(summary, "Shared scan")


@mcp.tool()
async def run_package_flow(ctx: Context, category: str = "ALL", force_refresh: bool = False) -> str:
    """Runs shared scan outputs and updates package-owned readiness outputs only."""
    state = ctx.request_context.lifespan_context
    await ctx.info(f"Starting package flow for category='{category}'...")
    summary = await _run_pipeline(state, category, force_refresh=force_refresh, workflow_scope="package")
    if "error" in summary:
        return f"Package flow failed: {summary['error']}"
    return _format_pipeline_summary(summary, "Package flow")


@mcp.tool()
async def run_qa_flow(ctx: Context, category: str = "ALL", force_refresh: bool = False) -> str:
    """Runs shared scan outputs and updates QA-owned validation/testcase outputs only."""
    state = ctx.request_context.lifespan_context
    await ctx.info(f"Starting QA flow for category='{category}'...")
    summary = await _run_pipeline(state, category, force_refresh=force_refresh, workflow_scope="qa")
    if "error" in summary:
        return f"QA flow failed: {summary['error']}"
    return _format_pipeline_summary(summary, "QA flow")


# ---------------------------------------------------------------------------
# Tool 5 — Run Full Pipeline
# ---------------------------------------------------------------------------
@mcp.tool()
async def run_full_pipeline(ctx: Context, category: str = "ALL", force_refresh: bool = False) -> str:
    """
    Runs the complete pipeline in sequence:
      1. Fetch latest versions from web
      2. Extract current versions from PDF
      3. Compare versions
      4. Send email notification

    This is also what the built-in scheduler calls automatically
    based on the cron schedule in config.json.
    """
    state    = ctx.request_context.lifespan_context
    await ctx.info(
        f"Starting full pipeline for category='{category}' "
        f"with cache_mode={'fresh' if force_refresh else 'use_cache'}..."
    )

    summary = await _run_pipeline(state, category, force_refresh=force_refresh, workflow_scope="full")

    if "error" in summary:
        return f"Pipeline failed: {summary['error']}"

    return _format_pipeline_summary(summary, "Pipeline")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("VersionManagerServer: starting.")
    # LOCAL: stdio transport (Claude Desktop connects via config)
    # CLOUD: change to mcp.run(transport="sse", host="0.0.0.0", port=8000)
    mcp.run(transport="stdio")
