from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from App.auth import current_user
from App.scan_reports import load_vulnerability_intelligence
from App.workspace import active_config, active_output_path, active_release_line, active_team_name
from Core.dependency_review import (
    REVIEW_STATUSES,
    apply_review_to_gate_decision,
    build_review_request,
    load_dependency_reviews,
    review_for_software,
    save_dependency_reviews,
    send_dependency_review_email,
)
from Utils.utils import load_config


def render_release_readiness_gate(ctx: Any) -> None:
    ctx.section_title(
        "Release Decision Gate",
        "Final go/no-go view combining package readiness, vulnerability risk, QA status, owners, approvals, and next actions.",
    )
    output_dir = active_output_path("__placeholder__").parent
    team = active_team_name()
    release = active_release_line(team)
    config = active_config(load_config())
    package_readiness = ctx.load_json(str(output_dir / "package_readiness.json"), ctx.file_mtime(output_dir / "package_readiness.json"))
    qa_validation = ctx.load_json(str(output_dir / "qa_validation.json"), ctx.file_mtime(output_dir / "qa_validation.json"))
    intelligence = load_vulnerability_intelligence(output_dir)
    reviews = load_dependency_reviews(output_dir)
    if not package_readiness:
        st.info("Package readiness data is not available yet. Run version comparison/package readiness first.")
        return

    rows = _build_gate_rows(package_readiness, qa_validation, intelligence, reviews)
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
        "Review Status",
        "Next Action",
    ]
    st.dataframe(ctx.style_operational_table(gate_df[display_cols]), use_container_width=True, hide_index=True)

    review_needed = gate_df[gate_df["Gate Decision"].isin(["Blocked", "QA Validation Required", "Review Required"])]
    st.subheader("Priority Actions")
    if review_needed.empty:
        st.success("No release-blocking package, vulnerability, or QA gate issue is currently identified.")
    else:
        for _, row in review_needed.head(6).iterrows():
            st.markdown(
                f"""
                <div class="vm-card">
                    <strong>{row['Software']} - {row['Gate Decision']}</strong>
                    <div class="vm-posture-note">
                        Owner: {row['Owner']} | Required approval: {row['Required Approval']} | Review: {row['Review Status']}<br>
                        {row['Reason']}<br>
                        {row['Next Action']}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    _render_dependency_review_workflow(
        gate_df=gate_df,
        package_readiness=package_readiness,
        intelligence=intelligence,
        reviews=reviews,
        output_dir=output_dir,
        team=team,
        release=release,
        config=config,
    )


def _build_gate_rows(
    package_readiness: dict[str, Any],
    qa_validation: dict[str, Any],
    intelligence: dict[str, Any],
    reviews: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    finding_lookup = _finding_lookup(intelligence)
    reviews = reviews or {}
    rows: list[dict[str, Any]] = []
    for software, package_record in package_readiness.items():
        if not isinstance(package_record, dict):
            continue
        name = str(package_record.get("Software Name") or software)
        finding = finding_lookup.get(name.lower(), {})
        qa_record = _record_for(name, qa_validation)
        gate = _gate_decision(name, package_record, qa_record, finding)
        review = review_for_software(reviews, name)
        if review:
            gate = apply_review_to_gate_decision(gate, review)
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
                "Review Status": review.get("status", "Not Requested") if review else "Not Requested",
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


def _render_dependency_review_workflow(
    *,
    gate_df: pd.DataFrame,
    package_readiness: dict[str, Any],
    intelligence: dict[str, Any],
    reviews: dict[str, dict[str, Any]],
    output_dir: Any,
    team: str,
    release: str,
    config: dict[str, Any],
) -> None:
    actionable = gate_df[gate_df["Gate Decision"].isin(["Blocked", "QA Validation Required", "Review Required"])]
    st.subheader("Dependency Review Workflow")
    st.caption(
        "Use this lightweight workflow when a package needs dependency, owner, security, platform, or QA review. "
        "EPRA sends the request and stores review status as release evidence."
    )
    if actionable.empty and not reviews:
        st.info("No dependency review request is currently required.")
        return

    finding_lookup = _finding_lookup(intelligence)
    review_rows = []
    for _, row in gate_df.iterrows():
        review = review_for_software(reviews, str(row["Software"]))
        if review or row["Gate Decision"] in {"Blocked", "QA Validation Required", "Review Required"}:
            review_rows.append(
                {
                    "Software": row["Software"],
                    "Gate Decision": row["Gate Decision"],
                    "Responsible Team": review.get("responsible_team", row["Required Approval"]) if review else row["Required Approval"],
                    "Recipient": review.get("recipient", ""),
                    "Review Status": review.get("status", "Not Requested") if review else "Not Requested",
                    "Email Status": review.get("email_status", "Not Sent") if review else "Not Sent",
                    "Updated At": review.get("updated_at", "") if review else "",
                }
            )
    if review_rows:
        st.dataframe(pd.DataFrame(review_rows), use_container_width=True, hide_index=True)

    options = [str(row["Software"]) for _, row in actionable.iterrows()]
    if not options:
        options = sorted({str(review.get("software")) for review in reviews.values() if review.get("software")})
    if not options:
        return

    with st.expander("Manage Dependency Review Request", expanded=False):
        selected = st.selectbox("Software", options, key="dependency_review_software")
        gate_row = gate_df[gate_df["Software"].astype(str) == selected].iloc[0].to_dict()
        package_record = _record_for(selected, package_readiness)
        finding = finding_lookup.get(selected.lower(), {})
        existing = review_for_software(reviews, selected)
        requested_by = str(current_user().get("username") or current_user().get("display_name") or "Release Coordinator")
        draft = existing or build_review_request(
            team=team,
            release=release,
            software=selected,
            gate_row=gate_row,
            package_record=package_record,
            finding=finding,
            config=config,
            requested_by=requested_by,
        )
        col1, col2 = st.columns(2)
        col1.text_input("Responsible Team", value=str(draft.get("responsible_team", "")), disabled=True)
        col2.text_input("Recipient", value=str(draft.get("recipient", "")), disabled=True)
        st.text_area("Evidence Required", value=str(draft.get("evidence_required", "")), disabled=True)

        selected_status = st.selectbox(
            "Review Status",
            REVIEW_STATUSES,
            index=REVIEW_STATUSES.index(str(draft.get("status", "Not Requested"))) if str(draft.get("status", "Not Requested")) in REVIEW_STATUSES else 0,
        )
        note = st.text_area("Response / Evidence Note", value=str(draft.get("response_note", "")))
        evidence_file = st.file_uploader("Attach Review Evidence", type=["txt", "pdf", "docx", "xlsx", "csv", "json", "png", "jpg", "jpeg"])
        buttons = st.columns(3)
        if buttons[0].button("Save Review Status", use_container_width=True):
            _save_review_update(output_dir, reviews, draft, selected_status, note, evidence_file)
            st.success("Dependency review status saved.")
            st.rerun()
        if buttons[1].button("Send Review Request", use_container_width=True):
            draft["status"] = "Requested"
            draft["response_note"] = note
            sent, error = send_dependency_review_email(draft)
            draft["email_status"] = "Sent" if sent else "Failed"
            draft["email_error"] = error
            _save_review_update(output_dir, reviews, draft, "Requested", note, evidence_file)
            if sent:
                st.success(f"Review request sent to {draft.get('recipient')}.")
            else:
                st.error(f"Review request was not sent: {error}")
            st.rerun()
        if buttons[2].button("Refresh Gate", use_container_width=True):
            st.rerun()


def _save_review_update(
    output_dir: Any,
    reviews: dict[str, dict[str, Any]],
    review: dict[str, Any],
    status: str,
    note: str,
    evidence_file: Any,
) -> None:
    from datetime import datetime

    review = dict(review)
    review["status"] = status
    review["response_note"] = note
    review["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    if evidence_file is not None:
        evidence_dir = output_dir / "dependency_review_evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(ch if ch.isalnum() or ch in {".", "-", "_"} else "_" for ch in evidence_file.name)
        evidence_path = evidence_dir / f"{review['review_id']}_{safe_name}"
        evidence_path.write_bytes(evidence_file.getbuffer())
        review["evidence_file"] = str(evidence_path)
    reviews[str(review["review_id"])] = review
    save_dependency_reviews(output_dir, reviews)
