from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from Core.vulnerability_correlator import build_vulnerability_intelligence
from Core.vulnerability_evidence_resolver import discover_release_reports, resolve_vulnerability_evidence
from Core.vulnerability_normalizer import normalize_vulnerability_findings

SCAN_REPORT_DIR = "scan_reports"
PARSED_SCAN_FILE = "uploaded_scan_findings.json"
VULNERABILITY_INTELLIGENCE_FILE = "vulnerability_intelligence.json"
VULNERABILITY_EVIDENCE_META_FILE = "vulnerability_evidence_source.json"


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


def build_and_save_vulnerability_intelligence(
    output_dir: Path,
    findings: list[dict[str, Any]],
    comparison: dict[str, Any] | None = None,
    package_readiness: dict[str, Any] | None = None,
    qa_validation: dict[str, Any] | None = None,
    release: str = "",
) -> dict[str, Any]:
    normalized = normalize_vulnerability_findings(findings, release=release)
    intelligence = build_vulnerability_intelligence(
        normalized,
        comparison=comparison or {},
        package_readiness=package_readiness or {},
        qa_validation=qa_validation or {},
    )
    intelligence["evidence_source"] = {
        "active_source": "Uploaded Scanner Report",
        "trust_level": "High",
        "source_priority": 2,
        "fallback_used": False,
        "source_file": PARSED_SCAN_FILE,
        "message": "Using scanner evidence uploaded through the Vulnerability Assessment page.",
    }
    save_vulnerability_intelligence(output_dir, intelligence)
    save_vulnerability_evidence_metadata(output_dir, intelligence["evidence_source"])
    return intelligence


def build_from_release_report_if_available(
    release_input_dir: Path,
    output_dir: Path,
    comparison: dict[str, Any] | None = None,
    package_readiness: dict[str, Any] | None = None,
    qa_validation: dict[str, Any] | None = None,
    release: str = "",
) -> tuple[dict[str, Any], Path | None]:
    reports = discover_release_reports(release_input_dir, output_dir)
    if not reports:
        return {}, None
    selected = reports[0]
    findings = parse_scan_report(selected)
    save_parsed_scan_findings(output_dir, findings)
    normalized = normalize_vulnerability_findings(findings, release=release)
    intelligence = build_vulnerability_intelligence(
        normalized,
        comparison=comparison or {},
        package_readiness=package_readiness or {},
        qa_validation=qa_validation or {},
    )
    evidence_source = {
        "active_source": "Release Scanner Report",
        "trust_level": "High",
        "source_priority": 1,
        "fallback_used": False,
        "source_file": str(selected),
        "release_reports": [str(path) for path in reports],
        "message": "Using scanner evidence found in the selected release folder.",
    }
    intelligence["evidence_source"] = evidence_source
    save_vulnerability_intelligence(output_dir, intelligence)
    save_vulnerability_evidence_metadata(output_dir, evidence_source)
    return intelligence, selected


def save_vulnerability_intelligence(output_dir: Path, intelligence: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / VULNERABILITY_INTELLIGENCE_FILE
    path.write_text(json.dumps(intelligence, indent=2), encoding="utf-8")
    return path


def load_vulnerability_intelligence(output_dir: Path) -> dict[str, Any]:
    path = output_dir / VULNERABILITY_INTELLIGENCE_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save_vulnerability_evidence_metadata(output_dir: Path, metadata: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / VULNERABILITY_EVIDENCE_META_FILE
    path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return path


def load_vulnerability_evidence_metadata(output_dir: Path) -> dict[str, Any]:
    path = output_dir / VULNERABILITY_EVIDENCE_META_FILE
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def resolve_current_vulnerability_evidence(
    release_input_dir: Path,
    output_dir: Path,
    uploaded_findings_count: int,
    intelligence_available: bool,
    nvd_available: bool,
) -> dict[str, Any]:
    metadata = load_vulnerability_evidence_metadata(output_dir)
    if metadata and intelligence_available:
        return metadata
    resolved = resolve_vulnerability_evidence(
        release_input_dir,
        output_dir,
        uploaded_findings_count=uploaded_findings_count,
        intelligence_available=intelligence_available,
        nvd_available=nvd_available,
    )
    if resolved["active_source"] in {"NVD Fallback", "No Vulnerability Evidence"}:
        save_vulnerability_evidence_metadata(output_dir, resolved)
    return resolved


def load_parsed_scan_findings(output_dir: Path) -> list[dict[str, Any]]:
    path = output_dir / PARSED_SCAN_FILE
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [_repair_scanner_source(item) for item in data if isinstance(item, dict)]


def _infer_scanner_source(record: dict[str, Any]) -> str:
    text = " ".join(
        str(record.get(key, ""))
        for key in ("Source File", "Evidence URL", "Description", "Scanner Source")
    ).lower()
    if "nexus" in text:
        return "Nexus IQ"
    if "snyk" in text:
        return "Snyk"
    if "blackduck" in text or "black duck" in text:
        return "Black Duck"
    if "trivy" in text:
        return "Trivy"
    if "qualys" in text:
        return "Qualys"
    if "tenable" in text or "nessus" in text:
        return "Tenable"
    if "veracode" in text:
        return "Veracode"
    return ""


def _repair_scanner_source(record: dict[str, Any]) -> dict[str, Any]:
    scanner_source = str(record.get("Scanner Source", "")).strip()
    if scanner_source and scanner_source.lower() not in {"uploaded scan report", "uploaded scanner report"}:
        return record
    inferred = _infer_scanner_source(record)
    if inferred:
        repaired = dict(record)
        repaired["Scanner Source"] = inferred
        return repaired
    return record


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
        "CVSS Score": _first(record, "cvss score", "cvss_score", "cvss", "score"),
        "Fixed Version": _first(record, "fixed version", "fixed_version", "remediation version", "patched version"),
        "Exploit Available": _first(record, "exploit available", "exploit_available", "known exploit", "exploit"),
        "EPSS Score": _first(record, "epss score", "epss_score", "epss"),
        "Description": _first(record, "description", "summary", "title", "finding"),
        "Scanner Source": _first(
            record,
            "Scanner Source",
            "scanner_source",
            "scanner source",
            "source_tool",
            "scanner",
            "tool",
            "source",
            default="Uploaded Scan Report",
        ),
        "Evidence URL": _first(record, "evidence url", "evidence_url", "url", "link"),
        "Source File": source_file,
        "Parsed At": datetime.now().isoformat(timespec="seconds"),
    }
