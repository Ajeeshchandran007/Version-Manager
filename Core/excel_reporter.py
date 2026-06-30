"""Excel workbook generation for software version assessments."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import pandas as pd


EXCEL_COLUMNS = [
    "Software Name",
    "Vendor",
    "Current Version",
    "Latest Version",
    "Version Status",
    "Risk Level (Low/Medium/High/Critical)",
    "Highest CVE Severity",
    "Upgrade Recommendation",
    "Need Update (Yes/No)",
    "Last Scan Date",
]


def generate_excel_report(
    comparison: dict[str, dict[str, Any]],
    vulnerabilities: dict[str, dict[str, Any]],
    output_path: str,
    scan_date: datetime | None = None,
) -> str:
    """Create Software_Version_Assessment.xlsx from comparison and security data."""
    scan_date = scan_date or datetime.now()
    rows = [
        _build_row(software, result, vulnerabilities.get(software, {}), scan_date)
        for software, result in comparison.items()
    ]

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    df = pd.DataFrame(rows, columns=EXCEL_COLUMNS)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Assessment")
        worksheet = writer.sheets["Assessment"]
        for column_cells in worksheet.columns:
            max_length = max(len(str(cell.value or "")) for cell in column_cells)
            worksheet.column_dimensions[column_cells[0].column_letter].width = min(max_length + 2, 60)

    return output_path


def _build_row(
    software: str,
    comparison: dict[str, Any],
    vulnerability: dict[str, Any],
    scan_date: datetime,
) -> dict[str, Any]:
    current = comparison.get("current") or {}
    latest = comparison.get("latest") or {}
    needs_update = bool(comparison.get("needs_update"))
    risk_level = _normalize_risk(vulnerability.get("risk_level"))

    return {
        "Software Name": software,
        "Vendor": _infer_vendor(software),
        "Current Version": _format_version(current),
        "Latest Version": _format_version(latest),
        "Version Status": _version_status(comparison),
        "Risk Level (Low/Medium/High/Critical)": risk_level,
        "Highest CVE Severity": _normalize_severity(vulnerability.get("severity")),
        "Upgrade Recommendation": _upgrade_recommendation(needs_update, risk_level),
        "Need Update (Yes/No)": "Yes" if needs_update else "No",
        "Last Scan Date": scan_date.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _format_version(version_info: dict[str, Any]) -> str:
    build = version_info.get("Build Version") or "Unknown"
    cu = version_info.get("Cumulative Update (CU)")
    return f"{build} ({cu})" if cu else build


def _upgrade_recommendation(needs_update: bool, risk_level: str) -> str:
    if risk_level == "Critical":
        return "Immediate upgrade required; critical security risk identified."
    if risk_level == "High":
        return "Prioritize upgrade in the next maintenance window."
    if needs_update:
        return "Upgrade to the latest approved vendor release."
    return "No upgrade required."


def _version_status(comparison: dict[str, Any]) -> str:
    if comparison.get("unknown"):
        return "Unknown"
    if comparison.get("needs_update"):
        return "Outdated"
    return "Current"


def _normalize_risk(risk: str | None) -> str:
    normalized = (risk or "Low").strip().lower()
    return {
        "critical": "Critical",
        "high": "High",
        "medium": "Medium",
        "low": "Low",
    }.get(normalized, "Low")


def _normalize_severity(severity: str | None) -> str:
    normalized = (severity or "None").strip().lower()
    return {
        "critical": "Critical",
        "high": "High",
        "medium": "Medium",
        "low": "Low",
        "none": "No CVEs",
        "potential": "Potential",
        "unknown": "Unknown",
    }.get(normalized, "Not Assessed")




def _infer_vendor(software: str) -> str:
    name = software.lower()
    if "sql server" in name or "exchange" in name or "outlook" in name or name == "edge":
        return "Microsoft"
    if "hcl" in name:
        return "HCL"
    if "elastic" in name:
        return "Elastic"
    if "openssl" in name:
        return "OpenSSL"
    if "curl" in name:
        return "curl"
    return "Unknown"
