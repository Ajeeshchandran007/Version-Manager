from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from App.formatting import format_epoch_ts, format_ts, parse_ts
from App.server_config import load_server_configs
from App.workspace import (
    BASE_DIR,
    active_config,
    active_output_path,
    active_release_line,
    active_team_name,
    project_path,
    team_input_software_path,
)
from App.workflow_actions import resolve_vendor_compatibility_requirements, run_async
from Core.notifier import is_actionable_update
from Core.workspace_assessment import build_qa_validation
from Utils.software_loader import load_software_metadata
from Utils.utils import load_config, logger
from Utils.version_format import canonical_version


METRICS_FILE = BASE_DIR / "output" / "metrics.jsonl"


def file_mtime(path: Path) -> float:
    return path.stat().st_mtime if path.exists() else 0.0


@st.cache_data
def load_json(path: str, mtime: float = 0.0) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        return {}
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


@st.cache_data
def load_metrics(path: str, mtime: float = 0.0) -> pd.DataFrame:
    file_path = Path(path)
    rows: list[dict[str, Any]] = []
    if not file_path.exists():
        return pd.DataFrame()
    for line in file_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return pd.DataFrame(rows)


@st.cache_data
def load_file_text(path: str, mtime: float = 0.0) -> str:
    file_path = Path(path)
    if not file_path.exists():
        return ""
    return file_path.read_text(encoding="utf-8", errors="ignore")



def value(record: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return default


def display_version(software_name: str, record: dict[str, Any], *keys: str) -> str:
    return canonical_version(software_name, value(record, *keys))


def safe_int(raw: Any, default: int = 0) -> int:
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return default


def inventory_status(record: dict[str, Any]) -> str:
    build_version = str(value(record, "Build Version", "version", default="")).strip()
    source = str(value(record, "source", default="")).lower()
    if not build_version or build_version.lower() in {"unknown", "none", "null"}:
        return "Unknown"
    if "unreachable" in source:
        return "Discovered via Document"
    return "Discovered"


def vendor_for(name: str) -> str:
    lookup = {
        "sql": "Microsoft",
        "exchange": "Microsoft",
        "outlook": "Microsoft",
        "edge": "Microsoft",
        "openssl": "OpenSSL",
        "libcurl": "curl",
        "hcl": "HCL",
        "elastic": "Elastic",
    }
    lowered = name.lower()
    for key, vendor in lookup.items():
        if key in lowered:
            return vendor
    return "Unknown"


def version_gap(current_version: str, latest_version: str, current_cu: str = "", latest_cu: str = "") -> str:
    current_version = str(current_version or "").strip()
    latest_version = str(latest_version or "").strip()
    current_cu = str(current_cu or "").strip()
    latest_cu = str(latest_cu or "").strip()
    if not current_version or not latest_version:
        return "Unknown"
    if current_version.lower() == latest_version.lower() and current_cu.lower() == latest_cu.lower():
        return "None"
    if current_version.lower() == latest_version.lower() and current_cu and latest_cu and current_cu.lower() != latest_cu.lower():
        return "CU Gap"
    if mixed_version_scheme(current_version, latest_version):
        return "Source Review"
    current_major = current_version.split(".")[0]
    latest_major = latest_version.split(".")[0]
    if current_major and latest_major and current_major != latest_major:
        return "Major Gap"
    return "Patch Gap"


def update_priority(gap: str, risk: str) -> str:
    risk = risk.upper()
    if risk in {"CRITICAL", "HIGH"}:
        return risk.title()
    if gap in {"Major Gap", "CU Gap", "Source Review"}:
        return "Medium"
    if gap in {"Patch Gap", "Minor Gap"}:
        return "Low"
    return "None"


def mixed_version_scheme(current_version: str, latest_version: str) -> bool:
    current_parts = [int(part) for part in re.findall(r"\d+", str(current_version or ""))[:4]]
    latest_parts = [int(part) for part in re.findall(r"\d+", str(latest_version or ""))[:4]]
    if not current_parts or not latest_parts:
        return False
    if current_parts[0] == latest_parts[0]:
        return False
    return max(current_parts[0], latest_parts[0]) >= 100 or abs(current_parts[0] - latest_parts[0]) >= 50


def load_workspace_outputs() -> tuple[dict[str, Any], dict[str, Any]]:
    config = active_config(load_config())
    package_path = project_path(config["output_files"].get("package_readiness_json", "output/package_readiness.json"))
    qa_path = project_path(config["output_files"].get("qa_validation_json", "output/qa_validation.json"))
    return load_json(str(package_path), file_mtime(package_path)), load_json(str(qa_path), file_mtime(qa_path))


def build_transient_compatibility_df(
    comparison: dict[str, Any],
    latest: dict[str, Any],
    vendor_requirements: dict[str, dict[str, Any]] | None = None,
) -> pd.DataFrame:
    if not comparison:
        return pd.DataFrame()
    config = active_config(load_config())
    compatibility = build_qa_validation(
        comparison,
        {},
        load_software_metadata(config["input_files"]["software_yml"], "ALL"),
        vendor_requirements,
    )
    return normalize_qa_validation(compatibility)



def load_vendor_compatibility_requirements(
    comparison: dict[str, Any],
    latest: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    try:
        return run_async(resolve_vendor_compatibility_requirements(comparison, latest, force_refresh=False))
    except Exception as exc:
        logger.warning("Vendor compatibility extraction unavailable: %s", exc)
        return {}


def normalize_current(data: dict[str, Any]) -> pd.DataFrame:
    rows = []
    fallback_scan_time = format_epoch_ts(file_mtime(active_output_path("current_versions.json")))
    server_configs = load_server_configs(active_config(load_config()))
    for name, record in data.items():
        source = value(record, "source", default="Unknown")
        rows.append(
            {
                "Software Name": name,
                "Vendor": vendor_for(name),
                "Current Version": display_version(name, record, "Build Version", "version"),
                "Current CU": value(record, "Cumulative Update (CU)", "cu", default=""),
                "Server Name": inventory_source_label(name, str(source), server_configs),
                "Environment": "Production",
                "Last Scanned": format_ts(value(record, "last_scanned", default="")) if value(record, "last_scanned", default="") else fallback_scan_time,
                "Source": source,
            }
        )
    return pd.DataFrame(rows)


def inventory_source_label(
    software_name: str,
    source: str,
    server_configs: dict[str, Any] | None = None,
) -> str:
    source_value = str(source or "").lower()
    if source_value == "live server":
        return "Configured Server" if software_name in (server_configs or {}) else "PDF Inventory"
    return "PDF Inventory" if "pdf" in source_value or "fallback" in source_value else "Inventory Evidence"


def configured_inventory_from_active_software_yml() -> tuple[pd.DataFrame, Path]:
    input_path = team_input_software_path(active_team_name(), active_release_line())
    metadata = load_software_metadata(str(input_path), "ALL") if input_path.exists() else {}
    rows = []
    for name, details in metadata.items():
        requirements = details.get("current_requirements", {}) if isinstance(details, dict) else {}
        requirements = requirements if isinstance(requirements, dict) else {}
        rows.append(
            {
                "Software Name": name,
                "Vendor": vendor_for(name),
                "Current Version": "Pending scan",
                "Current CU": "",
                "Server Name": "Pending scan",
                "Environment": "Production",
                "Last Scanned": "Not scanned",
                "Source": "software.yml",
                "OS Requirement": requirements.get("os", ""),
                "Database Requirement": requirements.get("database_version", ""),
                "Architecture": requirements.get("architecture", ""),
            }
        )
    return pd.DataFrame(rows), input_path


def normalize_latest(data: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for name, record in data.items():
        metadata = record.get("cache_metadata", {})
        rows.append(
            {
                "Software Name": name,
                "Vendor": vendor_for(name),
                "Latest Version": display_version(name, record, "Build Version", "version"),
                "Latest CU": value(record, "Cumulative Update (CU)", "cu", default=""),
                "Last Checked": format_ts(metadata.get("last_updated")),
                "Source": metadata.get("source", value(record, "source", default="vendor_sources")),
                "Cache Status": str(metadata.get("status", "unknown")).title(),
            }
        )
    return pd.DataFrame(rows)


def normalize_comparison(data: dict[str, Any], vulnerabilities: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for name, record in data.items():
        current = record.get("current", {})
        latest = record.get("latest", {})
        current_version = display_version(name, current, "Build Version", "version")
        latest_version = display_version(name, latest, "Build Version", "version")
        current_cu = value(current, "Cumulative Update (CU)", "cu", default="")
        latest_cu = value(latest, "Cumulative Update (CU)", "cu", default="")
        gap = version_gap(str(current_version), str(latest_version), str(current_cu), str(latest_cu))
        needs_update = is_actionable_update(record)
        risk = value(vulnerabilities.get(name, {}), "risk_level", default="UNKNOWN").upper()
        rows.append(
            {
                "Software Name": name,
                "Current Version": current_version,
                "Latest Version": latest_version,
                "Current CU": current_cu,
                "Latest CU": latest_cu,
                "Version Gap": gap,
                "Need Update": "Yes" if needs_update else "No",
                "Update Priority": update_priority(gap, risk),
                "Status": "Source Review" if gap == "Source Review" else ("Outdated" if needs_update else "Up-to-date"),
                "Risk Level": risk,
            }
        )
    return pd.DataFrame(rows)


def normalize_vulnerabilities(data: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for name, record in data.items():
        cves = record.get("cves") or []
        rows.append(
            {
                "Software Name": value(record, "software_name", default=name),
                "Current Installed Version": canonical_version(name, value(record, "current_version", "version")),
                "Latest Available Version": canonical_version(name, value(record, "latest_version", default="")),
                "Version Assessed": value(record, "version_assessed", default="current"),
                "CVE Severity": value(record, "severity", default="UNKNOWN").upper(),
                "Risk Level": value(record, "risk_level", default="UNKNOWN").upper(),
                "CVE Count": len(cves),
                "Security Assessment": value(record, "assessment", default="No assessment available."),
                "Source": vulnerability_source_label(value(record, "source", default="Unknown")),
            }
        )
    return pd.DataFrame(rows)


def vulnerability_source_label(source: str) -> str:
    source_value = str(source or "Unknown").lower()
    labels = []
    if "nvd" in source_value and "error" not in source_value:
        labels.append("NVD")
    if "local-assessment" in source_value or "error" in source_value:
        labels.append("NVD unavailable fallback")
    if "policy" in source_value:
        labels.append("Policy baseline")
    return " + ".join(labels) if labels else str(source or "Unknown")


def normalize_package_readiness(data: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for name, record in data.items():
        software_name = value(record, "Software Name", default=name)
        rows.append(
            {
                "Software Name": software_name,
                "Vendor": value(record, "Vendor", default=vendor_for(name)),
                "Current Version": canonical_version(software_name, value(record, "Current Version")),
                "Target Version": canonical_version(software_name, value(record, "Target Version")),
                "Package Readiness": value(record, "Package Readiness", default="Not Assessed"),
                "Upgrade Impact": value(record, "Upgrade Impact", default="Medium"),
                "Installer Type": value(record, "Installer Type", default="Vendor Package"),
                "Owner": value(record, "Owner", default="Application Owner"),
                "Release Notes": value(record, "Release Notes", default="Review vendor release notes."),
                "Download Link": value(record, "Download Link", default="Vendor portal"),
                "Blocker": value(record, "Blocker", default=""),
            }
        )
    return pd.DataFrame(rows)


def normalize_qa_validation(data: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for name, record in data.items():
        test_case_count = safe_int(value(record, "Test Case Count", default=0))
        test_cases_passed = safe_int(value(record, "Test Cases Passed", default=0))
        test_cases_failed = safe_int(value(record, "Test Cases Failed", default=0))
        test_cases_blocked = safe_int(value(record, "Test Cases Blocked / Not Tested", default=0))
        calculated_executed = test_cases_passed + test_cases_failed + test_cases_blocked
        raw_executed = safe_int(value(record, "Test Cases Executed", default=0))
        test_cases_executed = calculated_executed or raw_executed
        test_cases_executed = min(test_cases_executed, test_case_count) if test_case_count else test_cases_executed
        test_case_coverage = (
            f"{((test_cases_executed / test_case_count) * 100):.1f}%".replace(".0%", "%")
            if test_case_count
            else "Not Required"
        )
        rows.append(
            {
                "Software Name": value(record, "Software Name", default=name),
                "Current Version": canonical_version(name, value(record, "Current Version")),
                "Latest Version": canonical_version(name, value(record, "Target Version", "Package Version")),
                "Package Version": canonical_version(name, value(record, "Package Version")),
                "Installation Status": value(record, "Installation Status", default="Not Tested"),
                "Compatibility Status": value(record, "Compatibility Status", default="Review Required"),
                "Test Result": value(record, "Test Result", default="NOT TESTED"),
                "Supported OS": value(record, "Supported OS", "Windows Version", default="Not available"),
                "Supported Runtime": value(record, "Supported Runtime", ".NET Version", "Java Version", default="Not available"),
                "Supported Browser": value(record, "Supported Browser", "Browser Version", default="Not available"),
                "Database Dependency": value(record, "Database Dependency", "Supported Database", "Database Version", default="Not available"),
                "Supported Architecture": value(record, "Supported Architecture", "OS Architecture", default="Not available"),
                "Configured Environment": value(record, "Current Environment", default="Not provided in software.yml"),
                "Current Environment": value(record, "Current Environment", default="Not provided in software.yml"),
                "Requirement Source": value(record, "Requirement Source", default="Built-in compatibility rule"),
                "Requirement Source URL": value(record, "Requirement Source URL", default=""),
                "Requirement Confidence": value(record, "Requirement Confidence", default="Not available"),
                "Last Verified": value(record, "Last Verified", default="Not available"),
                "Test Case Count": test_case_count,
                "Test Cases Passed": test_cases_passed,
                "Test Cases Failed": test_cases_failed,
                "Test Cases Blocked / Not Tested": test_cases_blocked,
                "Test Cases Executed": test_cases_executed,
                "Test Case Coverage %": test_case_coverage,
                "Test Notes": value(record, "Test Notes"),
                "Test Date": value(record, "Test Date", default=""),
                "Tested By": value(record, "Tested By", default=""),
                "Evidence File": value(record, "Evidence File", default=""),
                "QA Revision": safe_int(value(record, "QA Revision", default=0)),
                "Last QA Update": value(record, "Last QA Update", default=""),
                "Last QA Updated By": value(record, "Last QA Updated By", default=""),
            }
        )
    return pd.DataFrame(rows)


def normalize_testcase_impact(data: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for item in data.get("test_case_plan", []) or []:
        software_name = value(item, "Software Name")
        rows.append({
            "Software Name": software_name,
            "Current Version": canonical_version(software_name, value(item, "Current Version")),
            "Target Version": canonical_version(software_name, value(item, "Target Version")),
            "Test Case Source": value(item, "Test Case Source", "Test Coverage", default="Not Found"),
            "Test Case ID": value(item, "Test Case ID"),
            "Test Case Name": value(item, "Test Case Name"),
            "Test Type": value(item, "Test Type"),
            "Priority": value(item, "Priority", default=value(item, "Recommended Priority", default="High")),
            "Automation Status": value(item, "Automation Status"),
            "Owner": value(item, "Owner", default="QA Lead"),
            "Applicable Version": value(item, "Applicable Version"),
            "Precondition": value(item, "Precondition"),
            "Expected Result": value(item, "Expected Result"),
            "Recommendation": value(item, "Recommendation"),
        })
    return pd.DataFrame(rows)


def add_environment_readiness(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "Compatibility Status" not in df.columns:
        return df
    copy = df.copy()
    copy["Environment Readiness"] = copy["Compatibility Status"].replace({
        "Review Required": "Needs Environment Review",
        "Compatible": "Compatible / No Review Needed",
    })
    return copy


def compliance_score(comparison_df: pd.DataFrame) -> int:
    if comparison_df.empty:
        return 0
    compliant = (comparison_df["Need Update"] == "No").sum()
    return round((compliant / len(comparison_df)) * 100)


def last_scan_time(*datasets: dict[str, Any]) -> str:
    timestamps: list[datetime] = []
    for dataset in datasets:
        for record in dataset.values():
            if isinstance(record, dict):
                metadata = record.get("cache_metadata") or {}
                parsed = parse_ts(metadata.get("last_updated"))
                if parsed:
                    timestamps.append(parsed)
    if not timestamps and METRICS_FILE.exists():
        metrics = load_metrics(str(METRICS_FILE), file_mtime(METRICS_FILE))
        if not metrics.empty and "ts" in metrics:
            parsed_values = [parse_ts(str(ts)) for ts in metrics["ts"].dropna().tolist()]
            timestamps.extend([item for item in parsed_values if item])
    if not timestamps:
        return "Not available"
    return max(timestamps).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def next_scan_time(config: dict[str, Any]) -> str:
    schedule = config.get("schedule_cron", "")
    if schedule:
        return describe_cron(schedule)
    return (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")


def describe_cron(schedule: str) -> str:
    parts = schedule.split()
    if len(parts) != 5:
        return schedule or "Not configured"
    minute, hour, day_of_month, month, day_of_week = parts
    if minute.startswith("*/") and hour == "*" and day_of_month == "*" and month == "*" and day_of_week == "*":
        return f"Every {minute[2:]} minutes"
    if minute == "0" and hour.startswith("*/") and day_of_month == "*" and month == "*" and day_of_week == "*":
        return f"Every {hour[2:]} hours"
    day_names = {
        "0": "Sunday",
        "1": "Monday",
        "2": "Tuesday",
        "3": "Wednesday",
        "4": "Thursday",
        "5": "Friday",
        "6": "Saturday",
        "7": "Sunday",
    }
    if not minute.isdigit() or not hour.isdigit():
        return f"Custom schedule: {schedule}"
    time_text = f"{hour.zfill(2)}:{minute.zfill(2)}"
    if day_of_month == "*" and month == "*" and day_of_week == "*":
        return f"Daily at {time_text}"
    if day_of_month == "*" and month == "*" and day_of_week in day_names:
        return f"Every {day_names[day_of_week]} at {time_text}"
    if day_of_month != "*" and month == "*" and day_of_week == "*":
        return f"Monthly on day {day_of_month} at {time_text}"
    return f"Custom schedule: {schedule}"

