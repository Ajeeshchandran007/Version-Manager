"""Applies enterprise risk policy on top of raw CVE lookup results."""
from __future__ import annotations

import re
from typing import Any


RISK_RANK = {"UNKNOWN": 0, "NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


def apply_security_policy(
    finding: dict[str, Any],
    software_name: str,
    current: dict[str, Any],
    latest: dict[str, Any],
    needs_update: bool,
) -> dict[str, Any]:
    """Raise risk for unsupported or high-impact outdated software.

    NVD keyword matching can return zero CVEs for product/version strings even when
    the installed baseline is not acceptable for enterprise operations. This layer
    does not create fake CVEs; it adds an operational risk reason.
    """
    result = dict(finding)
    policy_findings = []
    name = software_name.lower()
    current_version = str(current.get("Build Version") or result.get("version") or "")
    current_cu = str(current.get("Cumulative Update (CU)") or "")
    latest_cu = str(latest.get("Cumulative Update (CU)") or "")

    if "openssl" in name and _version_starts_with(current_version, ("1.0.", "1.1.")):
        policy_findings.append({
            "risk_level": "HIGH",
            "severity": "UNSUPPORTED",
            "reason": "OpenSSL 1.0.x/1.1.x is below the supported enterprise baseline. Upgrade to a supported OpenSSL branch.",
        })

    if "exchange" in name and needs_update:
        cu_gap = _cu_gap(current_cu, latest_cu)
        risk = "HIGH" if cu_gap is None or cu_gap >= 1 else "MEDIUM"
        policy_findings.append({
            "risk_level": risk,
            "severity": "PATCH_BASELINE",
            "reason": "Microsoft Exchange is behind the approved CU/security update baseline. Prioritize patch review for messaging infrastructure.",
        })

    if "sql server" in name and needs_update:
        cu_gap = _cu_gap(current_cu, latest_cu)
        if cu_gap is not None and cu_gap >= 4:
            policy_findings.append({
                "risk_level": "HIGH",
                "severity": "PATCH_BASELINE",
                "reason": f"SQL Server is {cu_gap} cumulative update(s) behind the approved baseline.",
            })

    if not policy_findings:
        return result

    strongest = max(policy_findings, key=lambda item: RISK_RANK.get(item["risk_level"], 0))
    if RISK_RANK.get(strongest["risk_level"], 0) > RISK_RANK.get(str(result.get("risk_level", "LOW")).upper(), 0):
        result["risk_level"] = strongest["risk_level"]
        if str(result.get("severity", "NONE")).upper() in {"NONE", "LOW", "UNKNOWN", "POTENTIAL"}:
            result["severity"] = strongest["severity"]

    result["policy_findings"] = policy_findings
    result["policy_adjusted"] = True
    result["risk_basis"] = "POLICY_BASELINE"
    result["policy_reason"] = strongest["reason"]
    result["assessment"] = f"{result.get('assessment', '').rstrip()} Policy risk: {strongest['reason']}"
    result["source"] = f"{result.get('source', 'assessment')}+policy"
    return result


def _version_starts_with(version: str, prefixes: tuple[str, ...]) -> bool:
    normalized = version.strip().lower().lstrip("v")
    return any(normalized.startswith(prefix) for prefix in prefixes)


def _cu_gap(current_cu: str, latest_cu: str) -> int | None:
    current_num = _cu_number(current_cu)
    latest_num = _cu_number(latest_cu)
    if current_num is None or latest_num is None:
        return None
    return max(0, latest_num - current_num)


def _cu_number(value: str) -> int | None:
    match = re.search(r"CU\s*(\d+)", value or "", re.IGNORECASE)
    return int(match.group(1)) if match else None
