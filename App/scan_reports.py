from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

SCAN_REPORT_DIR = "scan_reports"
PARSED_SCAN_FILE = "uploaded_scan_findings.json"


def scan_report_dir(output_dir: Path) -> Path:
    return output_dir / SCAN_REPORT_DIR


def save_uploaded_scan_report(output_dir: Path, uploaded_file: Any) -> Path:
    target_dir = scan_report_dir(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(ch if ch.isalnum() or ch in {".", "-", "_"} else "_" for ch in uploaded_file.name)
    target = target_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_name}"
    target.write_bytes(uploaded_file.getbuffer())
    return target


def parse_scan_report(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        raw = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        if isinstance(raw, list):
            return [_normalize_record(item, path.name) for item in raw if isinstance(item, dict)]
        if isinstance(raw, dict):
            rows = raw.get("findings") or raw.get("vulnerabilities") or raw.get("results") or []
            if isinstance(rows, list):
                return [_normalize_record(item, path.name) for item in rows if isinstance(item, dict)]
            return [_normalize_record(raw, path.name)]
    if suffix == ".csv":
        return [_normalize_record(row, path.name) for row in pd.read_csv(path).fillna("").to_dict(orient="records")]
    if suffix in {".xlsx", ".xls"}:
        return [_normalize_record(row, path.name) for row in pd.read_excel(path).fillna("").to_dict(orient="records")]
    raise ValueError("Supported scan report formats are JSON, CSV, XLSX, and XLS.")


def save_parsed_scan_findings(output_dir: Path, findings: list[dict[str, Any]]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / PARSED_SCAN_FILE
    path.write_text(json.dumps(findings, indent=2), encoding="utf-8")
    return path


def load_parsed_scan_findings(output_dir: Path) -> list[dict[str, Any]]:
    path = output_dir / PARSED_SCAN_FILE
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _first(record: dict[str, Any], *keys: str, default: str = "") -> str:
    lowered = {str(key).strip().lower(): value for key, value in record.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value not in (None, ""):
            return str(value)
    return default


def _normalize_record(record: dict[str, Any], source_file: str) -> dict[str, Any]:
    severity = _first(record, "severity", "risk", "risk level", "cve severity", default="UNKNOWN").upper()
    cve = _first(record, "cve", "cve id", "cve_id", "vulnerability id", "id")
    return {
        "Software Name": _first(record, "software", "software name", "component", "package", "product", default="Unknown"),
        "Version": _first(record, "version", "current version", "installed version", "current installed version"),
        "CVE": cve,
        "Severity": severity,
        "Risk Level": severity,
        "Description": _first(record, "description", "summary", "title", "finding"),
        "Scanner Source": _first(record, "scanner", "tool", "source", default="Uploaded Scan Report"),
        "Source File": source_file,
        "Parsed At": datetime.now().isoformat(timespec="seconds"),
    }
