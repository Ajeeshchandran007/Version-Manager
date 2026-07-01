from __future__ import annotations

from pathlib import Path
from typing import Any

from App.auth import ROLE_QA_ENGINEER, current_role
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


def qa_report_attachments(config: dict[str, Any]) -> list[Path]:
    if current_role() != ROLE_QA_ENGINEER:
        return []
    testcase_plan = project_path(
        config["output_files"].get("testcase_impact_xlsx", "output/Test_Case_Impact_Assessment.xlsx")
    )
    return [testcase_plan] if testcase_plan.exists() and testcase_plan.is_file() else []
