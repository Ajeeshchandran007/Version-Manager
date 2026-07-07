from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from App.emailing import prepare_email_report_files, qa_report_attachments
from App.workspace import BASE_DIR, project_path
from Core.comparator import compare
from Core.excel_reporter import generate_excel_report
from Core.notifier import count_actionable_updates, get_last_email_error, send_email
from Core.testcase_impact import save_testcase_impact_outputs
from Core.workspace_assessment import build_compatibility_assessment, build_package_readiness, build_qa_validation
from Utils.software_loader import load_software, load_software_metadata
from agent.contracts import tool_result_envelope
from agent.memory import get_run_history as read_run_history
from agent.memory import log_audit
from mcp_server import _load_json, _run_pipeline, _save_json, _vulnerability_path

ResolveVendorRequirements = Callable[[dict, dict], Awaitable[dict[str, dict[str, Any]]]]


def build_streamlit_agent_tools(state: dict[str, Any], resolve_vendor_compatibility_requirements: ResolveVendorRequirements) -> dict[str, Any]:
    config = state["config"]
    release_context = state.get("release_context")
    context_data = release_context.as_dict() if release_context else {}
    reserved_contract_keys = {"success", "source", "message", "data", "paths", "errors", "source_type", "widget"}

    def legacy_fields(payload: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in payload.items() if key not in reserved_contract_keys}

    async def get_software_list(category: str = "ALL") -> dict[str, Any]:
        software = load_software(config["input_files"]["software_yml"], category)
        return tool_result_envelope(
            source="Used app tool: Software Inventory",
            message=f"Loaded {len(software)} software item(s).",
            data={"category": category, "software": software},
            context=context_data,
            category=category,
            software=software,
        )

    async def query_server(software_name: str) -> dict[str, Any]:
        result = await state["server_querier"].fetch(software_name)
        if result:
            result.setdefault("source", "live server")
        payload = result or {"source": "live server", "error": "No version returned"}
        return tool_result_envelope(
            success="error" not in payload,
            source="Used app tool: Current Version Query",
            data=payload,
            context=context_data,
            **legacy_fields(payload),
        )

    async def extract_from_pdf(software_name: str) -> dict[str, Any]:
        result = await state["pdf_reader"].fetch(software_name)
        result.setdefault("source", "PDF fallback")
        return tool_result_envelope(
            source="Used app tool: PDF Version Fallback",
            data=result,
            context=context_data,
            **legacy_fields(result),
        )

    async def search_latest_version(software_name: str, force_refresh: bool = False) -> dict[str, Any]:
        result = await state["version_fetcher"].fetch(software_name, force_refresh=force_refresh)
        return tool_result_envelope(
            source="Used app tool: Latest Version Research",
            data=result,
            context=context_data,
            **legacy_fields(result),
        )

    async def compare_versions(latest: dict | None = None, current: dict | None = None) -> dict[str, Any]:
        latest = latest or {}
        current = current or {}
        comparison = compare(latest, current)
        _save_json(latest, config["output_files"]["latest_version_json"])
        _save_json(current, config["output_files"]["current_version_json"])
        _save_json(comparison, config["output_files"]["comparison_report_json"])
        return tool_result_envelope(
            source="Used app tool: Version Comparison",
            message=f"Compared {len(comparison)} software item(s).",
            data=comparison,
            paths={"comparison_report": str(project_path(config["output_files"]["comparison_report_json"]))},
            context=context_data,
            **comparison,
        )

    async def get_run_history(software_name: str, limit: int = 5) -> dict[str, Any]:
        return tool_result_envelope(
            source="Used app tool: Agent Memory",
            data={"software_name": software_name, "history": read_run_history(software_name, limit)},
            context=context_data,
            software_name=software_name,
            history=read_run_history(software_name, limit),
        )

    async def check_vulnerabilities(
        software_name: str,
        version: str | None = None,
        needs_update: bool = False,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        result = await state["vulnerability_checker"].check(
            software_name,
            version,
            needs_update,
            force_refresh=force_refresh,
        )
        return tool_result_envelope(
            source="Used app tool: Vulnerability Assessment",
            data=result,
            context=context_data,
            **legacy_fields(result),
        )

    async def save_vulnerability_report(vulnerabilities: dict) -> dict[str, Any]:
        out = _vulnerability_path(config)
        _save_json(vulnerabilities, out)
        return tool_result_envelope(
            source="Used app tool: Vulnerability Report",
            message=f"Saved {len(vulnerabilities)} vulnerability record(s).",
            data={"saved": True, "path": str((BASE_DIR / out).resolve()), "total": len(vulnerabilities)},
            paths={"vulnerability_report": str((BASE_DIR / out).resolve())},
            context=context_data,
            saved=True,
            path=str((BASE_DIR / out).resolve()),
            total=len(vulnerabilities),
        )

    async def assess_package_readiness(
        comparison: dict | None = None,
        latest: dict | None = None,
        vulnerabilities: dict | None = None,
    ) -> dict[str, Any]:
        readiness = build_package_readiness(comparison or {}, latest or {}, vulnerabilities or {})
        out = config["output_files"].get("package_readiness_json", "output/package_readiness.json")
        _save_json(readiness, out)
        return {
            **tool_result_envelope(
                source="Used app tool: Package Readiness",
                message=f"Assessed package readiness for {len(readiness)} item(s).",
                data={"saved": True, "path": str((BASE_DIR / out).resolve()), "total": len(readiness), "package_readiness": readiness},
                paths={"package_readiness": str((BASE_DIR / out).resolve())},
                context=context_data,
            ),
            "saved": True,
            "path": str((BASE_DIR / out).resolve()),
            "total": len(readiness),
            "package_readiness": readiness,
        }

    async def save_package_readiness(package_readiness: dict) -> dict[str, Any]:
        out = config["output_files"].get("package_readiness_json", "output/package_readiness.json")
        _save_json(package_readiness, out)
        return tool_result_envelope(
            source="Used app tool: Package Readiness Save",
            data={"saved": True, "path": str((BASE_DIR / out).resolve()), "total": len(package_readiness)},
            paths={"package_readiness": str((BASE_DIR / out).resolve())},
            context=context_data,
            saved=True,
            path=str((BASE_DIR / out).resolve()),
            total=len(package_readiness),
        )

    async def run_package_flow(category: str = "ALL", force_refresh: bool = False) -> dict[str, Any]:
        summary = await _run_pipeline(state, category, force_refresh=force_refresh, workflow_scope="package")
        return tool_result_envelope(
            success="error" not in summary,
            source="Used app tool: Package Flow",
            message=str(summary.get("error") or "Package workflow completed."),
            data=summary,
            context=context_data,
            **summary,
        )

    async def get_package_dashboard() -> dict[str, Any]:
        path = config["output_files"].get("package_readiness_json", "output/package_readiness.json")
        readiness = _load_json(path) if Path(BASE_DIR / path).exists() else {}
        blocked = {
            name: item for name, item in readiness.items()
            if "block" in str(item.get("Package Readiness") or item.get("Readiness") or "").lower()
            or str(item.get("Blocker") or item.get("Blockers") or "").strip()
        }
        data = {"total": len(readiness), "blocked_count": len(blocked), "package_readiness": readiness}
        return tool_result_envelope(
            source="Used app tool: Package Dashboard",
            data=data,
            paths={"package_readiness": str(project_path(path))},
            context=context_data,
            **data,
        )

    async def get_package_readiness_summary() -> dict[str, Any]:
        dashboard = await get_package_dashboard()
        data = dashboard.get("data", dashboard)
        return tool_result_envelope(
            source="Used app tool: Package Readiness Summary",
            data=data,
            context=context_data,
            **data,
        )

    async def get_blocked_packages() -> dict[str, Any]:
        dashboard = await get_package_dashboard()
        readiness = dashboard.get("data", {}).get("package_readiness", {})
        blocked = {
            name: item for name, item in readiness.items()
            if "block" in str(item.get("Package Readiness") or item.get("Readiness") or "").lower()
            or str(item.get("Blocker") or item.get("Blockers") or "").strip()
        }
        data = {"total": len(blocked), "blocked_packages": blocked}
        return tool_result_envelope(source="Used app tool: Blocked Packages", data=data, context=context_data, **data)

    async def get_package_checklist() -> dict[str, Any]:
        data = {
            "checklist": [
                "Version comparison completed",
                "Vulnerability review completed",
                "Package readiness generated",
                "Blockers reviewed",
                "QA handoff prepared",
            ]
        }
        return tool_result_envelope(source="Used app tool: Package Checklist", data=data, context=context_data, **data)

    async def check_compatibility(
        comparison: dict | None = None,
        package_readiness: dict | None = None,
    ) -> dict[str, Any]:
        metadata = load_software_metadata(config["input_files"]["software_yml"], config.get("default_category", "ALL"))
        comparison = comparison or {}
        latest = _load_json(config["output_files"].get("latest_version_json", "output/latest_versions.json"))
        vendor_requirements = await resolve_vendor_compatibility_requirements(comparison, latest)
        compatibility = build_compatibility_assessment(comparison, package_readiness or {}, metadata, vendor_requirements)
        return tool_result_envelope(
            source="Used app tool: Compatibility Assessment",
            message=f"Checked compatibility for {len(compatibility)} item(s).",
            data={"saved": True, "total": len(compatibility), "compatibility": compatibility},
            context=context_data,
            saved=True,
            total=len(compatibility),
            compatibility=compatibility,
        )

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
            **tool_result_envelope(
                source="Used app tool: QA Validation",
                message=f"Generated QA validation for {len(qa_validation)} item(s).",
                data={"saved": True, "path": str((BASE_DIR / out).resolve()), "total": len(qa_validation), "qa_validation": qa_validation},
                paths={"qa_validation": str((BASE_DIR / out).resolve())},
                context=context_data,
            ),
            "saved": True,
            "path": str((BASE_DIR / out).resolve()),
            "total": len(qa_validation),
            "qa_validation": qa_validation,
        }

    async def save_qa_validation(qa_validation: dict) -> dict[str, Any]:
        out = config["output_files"].get("qa_validation_json", "output/qa_validation.json")
        _save_json(qa_validation, out)
        return tool_result_envelope(
            source="Used app tool: QA Validation Save",
            data={"saved": True, "path": str((BASE_DIR / out).resolve()), "total": len(qa_validation)},
            paths={"qa_validation": str((BASE_DIR / out).resolve())},
            context=context_data,
            saved=True,
            path=str((BASE_DIR / out).resolve()),
            total=len(qa_validation),
        )

    async def run_qa_flow(category: str = "ALL", force_refresh: bool = False) -> dict[str, Any]:
        summary = await _run_pipeline(state, category, force_refresh=force_refresh, workflow_scope="qa")
        return tool_result_envelope(
            success="error" not in summary,
            source="Used app tool: QA Flow",
            message=str(summary.get("error") or "QA workflow completed."),
            data=summary,
            context=context_data,
            **summary,
        )

    async def generate_testcase_impact(comparison: dict | None = None) -> dict[str, Any]:
        comparison = comparison or _load_json(config["output_files"]["comparison_report_json"])
        impact = save_testcase_impact_outputs(
            comparison,
            str(project_path(config["input_files"].get("testcase_repository_xlsx", "Input/testcaseRepository.xlsx"))),
            str(project_path(config["output_files"].get("testcase_impact_json", "output/testcase_impact.json"))),
            str(project_path(config["output_files"].get("testcase_impact_xlsx", "output/Test_Case_Impact_Assessment.xlsx"))),
        )
        return {
            **tool_result_envelope(
                source="Used app tool: Test Case Impact",
                message="Generated test case impact output.",
                data={"saved": True, "summary": impact.get("summary", {}), "testcase_impact": impact},
                paths={
                    "testcase_impact": str(project_path(config["output_files"].get("testcase_impact_json", "output/testcase_impact.json"))),
                    "testcase_impact_excel": str(project_path(config["output_files"].get("testcase_impact_xlsx", "output/Test_Case_Impact_Assessment.xlsx"))),
                },
                context=context_data,
            ),
            "saved": True,
            "path": str(project_path(config["output_files"].get("testcase_impact_json", "output/testcase_impact.json"))),
            "excel_path": str(project_path(config["output_files"].get("testcase_impact_xlsx", "output/Test_Case_Impact_Assessment.xlsx"))),
            "summary": impact.get("summary", {}),
            "testcase_impact": impact,
        }

    async def get_qa_dashboard() -> dict[str, Any]:
        qa_path = config["output_files"].get("qa_validation_json", "output/qa_validation.json")
        impact_path = config["output_files"].get("testcase_impact_json", "output/testcase_impact.json")
        qa_validation = _load_json(qa_path) if Path(BASE_DIR / qa_path).exists() else {}
        impact = _load_json(impact_path) if Path(BASE_DIR / impact_path).exists() else {}
        data = {
            "qa_validation_available": bool(qa_validation),
            "testcase_impact_available": bool(impact),
            "total_software": len(qa_validation),
            "testcase_summary": impact.get("summary", {}),
        }
        return tool_result_envelope(
            source="Used app tool: QA Dashboard",
            data=data,
            paths={"qa_validation": str(project_path(qa_path)), "testcase_impact": str(project_path(impact_path))},
            context=context_data,
            **data,
        )

    async def get_testcase_coverage(category: str | None = None) -> dict[str, Any]:
        impact = await generate_testcase_impact()
        summary = impact.get("summary", {})
        data = {"category": category or config.get("default_category", "ALL"), "summary": summary}
        return tool_result_envelope(source="Used app tool: Test Case Coverage", data=data, context=context_data, **data)

    async def get_failed_qa_items() -> dict[str, Any]:
        qa_path = config["output_files"].get("qa_validation_json", "output/qa_validation.json")
        qa_validation = _load_json(qa_path) if Path(BASE_DIR / qa_path).exists() else {}
        failed = {
            name: item for name, item in qa_validation.items()
            if str(item.get("Test Result") or "").upper() in {"FAIL", "FAILED", "WARNING", "BLOCKED", "NOT TESTED"}
        }
        data = {"total": len(failed), "failed_qa_items": failed}
        return tool_result_envelope(source="Used app tool: Failed QA Items", data=data, context=context_data, **data)

    async def get_qa_testers() -> dict[str, Any]:
        qa_path = config["output_files"].get("qa_validation_json", "output/qa_validation.json")
        qa_validation = _load_json(qa_path) if Path(BASE_DIR / qa_path).exists() else {}
        testers = {
            name: {
                "Tested By": item.get("Tested By"),
                "Test Date": item.get("Test Date"),
                "Test Result": item.get("Test Result"),
            }
            for name, item in qa_validation.items()
        }
        data = {"total": len(testers), "qa_testers": testers}
        return tool_result_envelope(source="Used app tool: QA Tester Details", data=data, context=context_data, **data)

    async def generate_excel_assessment() -> dict[str, Any]:
        comparison = _load_json(config["output_files"]["comparison_report_json"])
        vulnerability_path = _vulnerability_path(config)
        vulnerabilities = _load_json(vulnerability_path) if Path(BASE_DIR / vulnerability_path).exists() else {}
        excel_path = BASE_DIR / config["output_files"].get("excel_assessment", "output/Software_Version_Assessment.xlsx")
        generate_excel_report(comparison, vulnerabilities, str(excel_path))
        return tool_result_envelope(
            source="Used app tool: Excel Assessment",
            data={"saved": True, "path": str(excel_path)},
            paths={"excel_assessment": str(excel_path)},
            context=context_data,
            saved=True,
            path=str(excel_path),
        )

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
        data = {
            "sent": sent,
            "subject": subject,
            "attachments": [path.name for path in attachments],
            "error": get_last_email_error(),
        }
        return tool_result_envelope(
            success=sent,
            source="Used app tool: Notification",
            message="Notification sent." if sent else "Notification was not sent.",
            data=data,
            context=context_data,
            **data,
        )

    async def log_audit_event(step: str, details: dict | None = None) -> dict[str, Any]:
        run_id = (details or {}).get("run_id", "streamlit")
        log_audit(run_id, step, "streamlit", details or {})
        return tool_result_envelope(
            source="Used app tool: Audit Log",
            data={"logged": True, "run_id": run_id, "step": step},
            context=context_data,
            logged=True,
            run_id=run_id,
            step=step,
        )

    async def run_shared_scan(category: str = "ALL", force_refresh: bool = False) -> dict[str, Any]:
        summary = await _run_pipeline(state, category, force_refresh=force_refresh, workflow_scope="shared")
        return tool_result_envelope(
            success="error" not in summary,
            source="Used app tool: Shared Scan",
            message=str(summary.get("error") or "Shared scan completed."),
            data=summary,
            context=context_data,
            **summary,
        )

    async def get_output_files() -> dict[str, Any]:
        files = {}
        for key, path in config.get("output_files", {}).items():
            resolved = project_path(path)
            files[key] = {
                "path": str(resolved),
                "exists": resolved.exists(),
                "size_bytes": resolved.stat().st_size if resolved.exists() else 0,
            }
        data = {"total_configured": len(files), "total_existing": len([item for item in files.values() if item["exists"]]), "files": files}
        return tool_result_envelope(source="Used app tool: Output Files", data=data, context=context_data, **data)

    async def get_release_artifacts() -> dict[str, Any]:
        output_files = await get_output_files()
        data = {
            "active_team": context_data.get("team", ""),
            "active_release": context_data.get("release", ""),
            "artifacts": output_files.get("files", {}),
        }
        return tool_result_envelope(source="Used app tool: Release Artifacts", data=data, context=context_data, **data)

    return {
        "get_software_list": get_software_list,
        "query_server": query_server,
        "extract_from_pdf": extract_from_pdf,
        "search_latest_version": search_latest_version,
        "compare_versions": compare_versions,
        "get_run_history": get_run_history,
        "check_vulnerabilities": check_vulnerabilities,
        "save_vulnerability_report": save_vulnerability_report,
        "run_package_flow": run_package_flow,
        "assess_package_readiness": assess_package_readiness,
        "save_package_readiness": save_package_readiness,
        "get_package_dashboard": get_package_dashboard,
        "get_package_readiness_summary": get_package_readiness_summary,
        "get_blocked_packages": get_blocked_packages,
        "get_package_checklist": get_package_checklist,
        "check_compatibility": check_compatibility,
        "run_qa_flow": run_qa_flow,
        "generate_qa_validation": generate_qa_validation,
        "save_qa_validation": save_qa_validation,
        "generate_testcase_impact": generate_testcase_impact,
        "get_qa_dashboard": get_qa_dashboard,
        "get_testcase_coverage": get_testcase_coverage,
        "get_failed_qa_items": get_failed_qa_items,
        "get_qa_testers": get_qa_testers,
        "run_shared_scan": run_shared_scan,
        "generate_excel_assessment": generate_excel_assessment,
        "get_output_files": get_output_files,
        "get_release_artifacts": get_release_artifacts,
        "send_notification": send_notification,
        "log_audit_event": log_audit_event,
    }
