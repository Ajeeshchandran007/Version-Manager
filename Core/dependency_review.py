from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from Core.notifier import get_last_email_error, send_email


REVIEW_STATUSES = [
    "Not Requested",
    "Requested",
    "Approved",
    "Rejected",
    "Deferred",
    "Evidence Needed",
]


def load_dependency_reviews(output_dir: Path) -> dict[str, dict[str, Any]]:
    path = dependency_review_path(output_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save_dependency_reviews(output_dir: Path, reviews: dict[str, dict[str, Any]]) -> None:
    path = dependency_review_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reviews, indent=2), encoding="utf-8")


def dependency_review_path(output_dir: Path) -> Path:
    return output_dir / "dependency_reviews.json"


def review_id(team: str, release: str, software: str) -> str:
    safe = "".join(ch if ch.isalnum() else "-" for ch in software).strip("-")
    return f"REV-{team}-{release}-{safe}"


def review_for_software(reviews: dict[str, dict[str, Any]], software: str) -> dict[str, Any]:
    lowered = software.lower()
    for review in reviews.values():
        if str(review.get("software", "")).lower() == lowered:
            return review
    return {}


def identify_responsible_team(
    package_record: dict[str, Any],
    finding: dict[str, Any],
    gate_decision: str,
) -> str:
    owner = str(package_record.get("Owner") or finding.get("owner") or "").strip()
    package_status = str(package_record.get("Package Readiness") or "").lower()
    blocker = str(package_record.get("Blocker") or "").lower()
    software = str(package_record.get("Software Name") or finding.get("software_name") or "").lower()
    if finding.get("release_blocker") or "security" in blocker or "openssl" in software or "curl" in software:
        return "Security Team"
    if "qa" in gate_decision.lower():
        return "QA Team"
    if owner:
        return owner
    if "sql" in software:
        return "DBA Team"
    if "exchange" in software:
        return "Messaging Team"
    if "review" in package_status or blocker:
        return "Application Owner"
    return "Release Team"


def evidence_required(package_record: dict[str, Any], finding: dict[str, Any]) -> str:
    status = str(package_record.get("Package Readiness") or "")
    blocker = str(package_record.get("Blocker") or "")
    if finding.get("release_blocker"):
        return "Security impact decision, fixed-version confirmation, packaging plan, and QA validation evidence."
    if "dependency review" in status.lower():
        return "Dependency compatibility confirmation, upgrade prerequisites, install validation, rollback plan, and owner signoff."
    if blocker:
        return "Blocker resolution evidence, owner confirmation, and refreshed package readiness status."
    return "Review note and package/QA validation evidence."


def recipient_for_team(config: dict[str, Any], team: str) -> str:
    contacts = config.get("review_contacts") or {}
    if isinstance(contacts, dict) and contacts.get(team):
        return str(contacts[team])
    smtp_recipients = (config.get("smtp") or {}).get("recipients") or []
    return str(smtp_recipients[0]) if smtp_recipients else ""


def build_review_request(
    *,
    team: str,
    release: str,
    software: str,
    gate_row: dict[str, Any],
    package_record: dict[str, Any],
    finding: dict[str, Any],
    config: dict[str, Any],
    requested_by: str,
) -> dict[str, Any]:
    responsible = identify_responsible_team(package_record, finding, str(gate_row.get("Gate Decision", "")))
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "review_id": review_id(team, release, software),
        "team": team,
        "release": release,
        "software": software,
        "status": "Requested",
        "responsible_team": responsible,
        "recipient": recipient_for_team(config, responsible),
        "gate_decision": gate_row.get("Gate Decision", ""),
        "package_status": gate_row.get("Package Status", ""),
        "vulnerability_decision": gate_row.get("Vulnerability Decision", ""),
        "risk_score": gate_row.get("Risk Score", "N/A"),
        "reason": gate_row.get("Reason", ""),
        "evidence_required": evidence_required(package_record, finding),
        "requested_by": requested_by,
        "requested_at": now,
        "updated_at": now,
        "response_note": "",
        "evidence_file": "",
        "email_status": "Not Sent",
        "email_error": "",
    }


def send_dependency_review_email(review: dict[str, Any]) -> tuple[bool, str]:
    recipient = str(review.get("recipient") or "").strip()
    if not recipient:
        return False, "No review recipient is configured."
    subject = f"[EPRA Review Required] {review.get('team')} {review.get('release')} - {review.get('software')}"
    body = "\n".join(
        [
            "EPRA dependency review request",
            "",
            f"Software: {review.get('software')}",
            f"Release: {review.get('release')}",
            f"Responsible Team: {review.get('responsible_team')}",
            f"Gate Decision: {review.get('gate_decision')}",
            f"Package Status: {review.get('package_status')}",
            f"Vulnerability Decision: {review.get('vulnerability_decision')}",
            f"Risk Score: {review.get('risk_score')}",
            "",
            f"Reason: {review.get('reason')}",
            "",
            f"Evidence Required: {review.get('evidence_required')}",
            "",
            "Please respond with Approved, Rejected, Deferred, or Evidence Needed and include supporting notes/evidence.",
        ]
    )
    sent = send_email(subject, body, recipients=[recipient])
    return sent, "" if sent else (get_last_email_error() or "Email send failed.")


def apply_review_to_gate_decision(gate: dict[str, str], review: dict[str, Any]) -> dict[str, str]:
    status = str(review.get("status") or "")
    if status == "Approved" and gate.get("decision") in {"Review Required", "Blocked"}:
        updated = dict(gate)
        updated["decision"] = "Ready To Package"
        updated["reason"] = f"Dependency review approved for {review.get('software')}."
        updated["action"] = "Proceed with package approval workflow and retain review evidence."
        return updated
    if status == "Rejected":
        updated = dict(gate)
        updated["decision"] = "Blocked"
        updated["reason"] = f"Dependency review rejected for {review.get('software')}."
        updated["action"] = "Resolve rejection reason and request dependency review again."
        return updated
    if status == "Deferred":
        updated = dict(gate)
        updated["decision"] = "Proceed With Monitoring"
        updated["reason"] = f"Dependency review deferred for {review.get('software')} with risk acceptance."
        updated["action"] = "Proceed only with documented risk acceptance and monitoring."
        return updated
    if status == "Evidence Needed":
        updated = dict(gate)
        updated["decision"] = "Review Required"
        updated["reason"] = f"Additional dependency evidence is needed for {review.get('software')}."
        updated["action"] = "Collect requested evidence and update dependency review status."
        return updated
    return gate
