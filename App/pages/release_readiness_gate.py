from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from App.scan_reports import load_vulnerability_intelligence
from App.workspace import active_output_path


def render_release_readiness_gate(ctx: Any) -> None:
    ctx.section_title(
        "Release Readiness Gate",
        "Final go/no-go view combining package readiness, vulnerability risk, QA status, owners, approvals, and next actions.",
    )
    output_dir = active_output_path("__placeholder__").parent
    package_readiness = ctx.load_json(str(output_dir / "package_readiness.json"), ctx.file_mtime(output_dir / "package_readiness.json"))
    qa_validation = ctx.load_json(str(output_dir / "qa_validation.json"), ctx.file_mtime(output_dir / "qa_validation.json"))
    intelligence = load_vulnerability_intelligence(output_dir)
    if not package_readiness:
        st.info("Package readiness data is not available yet. Run version comparison/package readiness first.")
        return

    rows = _build_gate_rows(package_readiness, qa_validation, intelligence)
    gate_df = pd.DataFrame(rows)
    if gate_df.empty:
        st.info("No release packages are available for gate assessment.")
        return

    counts = gate_df["Gate Decision"].value_counts().to_dict()
    blocked = counts.get("Blocked", 0)
    qa_required = counts.get("QA Validation Required", 0)
    review_required = counts.get("Review Required", 0)
    ready = counts.get("Ready To Package", 0)
    monitoring = counts.get("Proceed With Monitoring", 0)
    overall = "Blocked" if blocked else ("Review Required" if qa_required or review_required else "Ready")

    cols = st.columns(5)
    cols[0].metric("Overall Release Decision", overall)
    cols[1].metric("Total Packages", len(gate_df))
    cols[2].metric("Blocked", blocked)
    cols[3].metric("Needs Review", review_required + qa_required)
    cols[4].metric("Ready / Track", ready + monitoring)

    st.caption(
        "Use this page as the final package/release decision view. Detailed scanner evidence, NVD fallback, QA, and package workflow data remain in their source pages."
    )
    display_cols = [
        "Software",
        "Gate Decision",
        "Reason",
        "Vulnerability Decision",
        "Risk Score",
        "Package Status",
        "QA Result",
        "Owner",
        "Required Approval",
        "Next Action",
    ]
    st.dataframe(ctx.style_operational_table(gate_df[display_cols]), use_container_width=True, hide_index=True)

    blockers = gate_df[gate_df["Gate Decision"].isin(["Blocked", "QA Validation Required", "Review Required"])]
    st.subheader("Priority Actions")
    if blockers.empty:
        st.success("No release-blocking package, vulnerability, or QA gate issue is currently identified.")
    else:
        for _, row in blockers.head(6).iterrows():
            st.markdown(
                f"""
                <div class="vm-card">
                    <strong>{row['Software']} - {row['Gate Decision']}</strong>
                    <div class="vm-posture-note">
                        Owner: {row['Owner']} | Required approval: {row['Required Approval']}<br>
                        {row['Reason']}<br>
                        {row['Next Action']}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def _build_gate_rows(
    package_readiness: dict[str, Any],
    qa_validation: dict[str, Any],
    intelligence: dict[str, Any],
) -> list[dict[str, Any]]:
    finding_lookup = _finding_lookup(intelligence)
    rows: list[dict[str, Any]] = []
    for software, package_record in package_readiness.items():
        if not isinstance(package_record, dict):
            continue
        name = str(package_record.get("Software Name") or software)
        finding = finding_lookup.get(name.lower(), {})
        qa_record = _record_for(name, qa_validation)
        gate = _gate_decision(name, package_record, qa_record, finding)
        rows.append(
            {
                "Software": name,
                "Gate Decision": gate["decision"],
                "Reason": gate["reason"],
                "Vulnerability Decision": finding.get("blocker_decision") or "No scanner CVE",
                "Risk Score": finding.get("release_risk_score") if finding else "N/A",
                "Package Status": package_record.get("Package Readiness", "Not Assessed"),
                "QA Result": qa_record.get("Test Result") or qa_record.get("Compatibility Status") or "Not Assessed",
                "Owner": package_record.get("Owner") or finding.get("owner") or "Not Assigned",
                "Required Approval": gate["approval"],
                "Next Action": gate["action"],
            }
        )
    order = {
        "Blocked": 0,
        "QA Validation Required": 1,
        "Review Required": 2,
        "Proceed With Monitoring": 3,
        "Ready To Package": 4,
    }
    return sorted(rows, key=lambda item: (order.get(str(item["Gate Decision"]), 9), str(item["Software"])))


def _gate_decision(name: str, package_record: dict[str, Any], qa_record: dict[str, Any], finding: dict[str, Any]) -> dict[str, str]:
    package_status = str(package_record.get("Package Readiness") or "").lower()
    package_blocker = str(package_record.get("Blocker") or "").strip()
    qa_result = str(qa_record.get("Test Result") or qa_record.get("Compatibility Status") or "").lower()
    owner = str(package_record.get("Owner") or finding.get("owner") or "Release Engineer")
    vulnerability_decision = str(finding.get("blocker_decision") or "")

    if finding.get("release_blocker"):
        return {
            "decision": "Blocked",
            "reason": f"{name} has a release-blocking vulnerability finding.",
            "approval": finding.get("owner") or owner,
            "action": finding.get("recommended_action") or "Resolve vulnerability blocker before release.",
        }
    if "block" in package_status or package_blocker:
        return {
            "decision": "Blocked",
            "reason": package_blocker or f"{name} package readiness is blocked.",
            "approval": owner,
            "action": "Resolve package blocker and refresh package readiness evidence.",
        }
    if "fail" in qa_result or "blocked" in qa_result or "not tested" in qa_result:
        return {
            "decision": "QA Validation Required",
            "reason": f"{name} requires QA validation before package/release decision.",
            "approval": "QA Team",
            "action": "Complete impacted QA validation and update QA signoff.",
        }
    if "review" in package_status or vulnerability_decision == "Security Review Required":
        approval = finding.get("owner") or owner
        return {
            "decision": "Review Required",
            "reason": f"{name} requires owner review before release/package approval.",
            "approval": approval,
            "action": "Complete owner review and update package/security decision.",
        }
    if vulnerability_decision in {"Monitor / Remediate", "Track"} or finding:
        return {
            "decision": "Proceed With Monitoring",
            "reason": f"{name} has no release-blocking finding in active evidence.",
            "approval": "Release Engineer",
            "action": finding.get("recommended_action") or "Proceed with package workflow and monitor security evidence.",
        }
    return {
        "decision": "Ready To Package",
        "reason": f"{name} has no scanner CVE blocker and no package/QA blocker.",
        "approval": "Release Engineer",
        "action": "Proceed with package approval workflow.",
    }


def _finding_lookup(intelligence: dict[str, Any]) -> dict[str, dict[str, Any]]:
    findings = intelligence.get("findings", []) if isinstance(intelligence, dict) else []
    lookup: dict[str, dict[str, Any]] = {}
    for item in findings:
        if not isinstance(item, dict) or not item.get("software_name"):
            continue
        key = str(item["software_name"]).lower()
        existing = lookup.get(key)
        if not existing or int(item.get("release_risk_score") or 0) > int(existing.get("release_risk_score") or 0):
            lookup[key] = item
    return lookup


def _record_for(name: str, source: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    record = source.get(name, {})
    if isinstance(record, dict):
        return record
    lowered = name.lower()
    for key, value in source.items():
        if str(key).lower() == lowered and isinstance(value, dict):
            return value
    return {}
