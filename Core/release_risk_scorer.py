from __future__ import annotations

from typing import Any

from Core.vulnerability_normalizer import SEVERITY_ORDER


def score_vulnerability(
    finding: dict[str, Any],
    package_record: dict[str, Any] | None = None,
    qa_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    package_record = package_record or {}
    qa_record = qa_record or {}
    severity = str(finding.get("severity") or "UNKNOWN").upper()
    cvss = float(finding.get("cvss_score") or 0)
    epss = float(finding.get("epss_score") or 0)
    package_status = str(package_record.get("Package Readiness") or package_record.get("status") or "").lower()
    package_blocker = str(package_record.get("Blocker") or package_record.get("blocker") or "")
    qa_result = str(qa_record.get("Test Result") or qa_record.get("Compatibility Status") or "").lower()
    qa_coverage = str(qa_record.get("Test Case Coverage %") or "")

    score = 0
    reasons: list[str] = []
    score += {"CRITICAL": 40, "HIGH": 30, "MEDIUM": 18, "LOW": 8}.get(severity, 4)
    reasons.append(f"{severity.title()} scanner severity")
    if cvss >= 9:
        score += 15
        reasons.append("CVSS >= 9")
    elif cvss >= 7:
        score += 10
        reasons.append("CVSS >= 7")
    if bool(finding.get("exploit_available")):
        score += 20
        reasons.append("known exploit available")
    if epss >= 0.7:
        score += 12
        reasons.append("high EPSS probability")
    elif epss >= 0.4:
        score += 6
        reasons.append("moderate EPSS probability")
    if "block" in package_status or package_blocker:
        score += 18
        reasons.append("package readiness blocked or has blocker")
    elif "review" in package_status:
        score += 10
        reasons.append("package review required")
    if "fail" in qa_result or "blocked" in qa_result:
        score += 12
        reasons.append("QA failed or blocked")
    elif "not tested" in qa_result or qa_coverage.startswith("0"):
        score += 8
        reasons.append("QA coverage pending")
    if not finding.get("fixed_version"):
        score += 8
        reasons.append("fixed version not recorded")

    score = min(score, 100)
    if score >= 80:
        band, decision = "Critical", "Release Blocker"
    elif score >= 55:
        band, decision = "High", "Security Review Required"
    elif score >= 30:
        band, decision = "Medium", "Monitor / Remediate"
    else:
        band, decision = "Low", "Track"
    return {
        "release_risk_score": score,
        "risk_band": band,
        "release_blocker": decision == "Release Blocker",
        "blocker_decision": decision,
        "risk_reasons": reasons,
    }


def severity_rank(severity: str) -> int:
    return SEVERITY_ORDER.get(str(severity or "UNKNOWN").upper(), 0)
