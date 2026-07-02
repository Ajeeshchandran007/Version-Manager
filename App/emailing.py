from __future__ import annotations

from pathlib import Path
from typing import Any

from App.auth import ROLE_QA_ENGINEER, ROLE_RELEASE_ENGINEER, current_role
from App.workspace import active_output_path, project_path
from Core.notifier import build_html_report, build_report


def prepare_email_report_files(
    comparison: dict[str, dict],
    vulnerabilities: dict[str, dict] | None = None,
) -> tuple[str, str]:
    body = build_report(comparison, vulnerabilities or {})
    html_body = build_html_report(comparison, vulnerabilities or {})
    text_file = active_output_path("email_preview.txt")
    html_file = active_output_path("email_preview.html")
    text_file.parent.mkdir(parents=True, exist_ok=True)
    text_file.write_text(body, encoding="utf-8")
    html_file.write_text(html_body, encoding="utf-8")
    return body, html_body


def role_report_attachments(config: dict[str, Any]) -> list[Path]:
    role = current_role()
    if role == ROLE_QA_ENGINEER:
        paths = [
            project_path(config["output_files"].get("testcase_impact_xlsx", "output/Test_Case_Impact_Assessment.xlsx")),
            project_path(config["output_files"].get("qa_validation_json", "output/qa_validation.json")),
        ]
    elif role == ROLE_RELEASE_ENGINEER:
        paths = [
            project_path(config["output_files"].get("excel_assessment", "output/Software_Version_Assessment.xlsx")),
            project_path(config["output_files"].get("package_readiness_json", "output/package_readiness.json")),
        ]
    else:
        paths = [
            project_path(config["output_files"].get("excel_assessment", "output/Software_Version_Assessment.xlsx")),
        ]
    return [path for path in paths if path.exists() and path.is_file()]


def qa_report_attachments(config: dict[str, Any]) -> list[Path]:
    return role_report_attachments(config)
