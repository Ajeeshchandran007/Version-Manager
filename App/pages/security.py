from __future__ import annotations

from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

from App.scan_reports import (
    build_from_release_report_if_available,
    build_and_save_vulnerability_intelligence,
    load_parsed_scan_findings,
    load_vulnerability_intelligence,
    parse_scan_report,
    resolve_current_vulnerability_evidence,
    save_parsed_scan_findings,
    save_uploaded_scan_report,
)
from App.workspace import active_output_path, active_release_line, active_team_name, team_input_software_path


RISK_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE", "UNKNOWN"]


def render_vulnerabilities(vuln_df: pd.DataFrame, ctx: Any) -> None:
    ctx.section_title("Vulnerability Assessment", "Security assessment of current installed versions with latest version context.")
    output_dir = active_output_path("__placeholder__").parent
    team = active_team_name()
    release = active_release_line(team)
    release_input_dir = team_input_software_path(team, release).parent
    parsed_scan_findings = load_parsed_scan_findings(output_dir)
    vulnerability_intelligence = load_vulnerability_intelligence(output_dir)
    comparison = ctx.load_json(str(output_dir / "comparison_report.json"), ctx.file_mtime(output_dir / "comparison_report.json"))
    package_readiness = ctx.load_json(str(output_dir / "package_readiness.json"), ctx.file_mtime(output_dir / "package_readiness.json"))
    qa_validation = ctx.load_json(str(output_dir / "qa_validation.json"), ctx.file_mtime(output_dir / "qa_validation.json"))
    evidence_source = resolve_current_vulnerability_evidence(
        release_input_dir,
        output_dir,
        uploaded_findings_count=len(parsed_scan_findings),
        intelligence_available=bool(vulnerability_intelligence),
        nvd_available=not vuln_df.empty,
    )
    findings = vulnerability_intelligence.get("findings", []) if vulnerability_intelligence else []
    summary = vulnerability_intelligence.get("summary", {}) if vulnerability_intelligence else {}
    highest_risk = max((int(item.get("release_risk_score") or 0) for item in findings if isinstance(item, dict)), default=0)
    release_blockers = int(summary.get("release_blockers", 0) or 0)
    overall_decision = "Blocked" if release_blockers else ("Review Required" if highest_risk >= 55 else "Track")

    st.subheader("Executive Decision Summary")
    source_cols = st.columns(5)
    source_cols[0].metric("Overall Decision", overall_decision)
    source_cols[1].metric("Release Blockers", release_blockers)
    source_cols[2].metric("Highest Risk Score", highest_risk)
    source_cols[3].metric("Evidence Source", evidence_source.get("active_source", "Unknown"))
    source_cols[4].metric("Trust", evidence_source.get("trust_level", "Unknown"))
    st.caption(
        f"Fallback used: {'Yes' if evidence_source.get('fallback_used') else 'No'} | "
        f"Source: {evidence_source.get('source_file') or 'Not available'} | "
        f"NVD fallback: {'Available' if not vuln_df.empty else 'Not available'} | "
        f"Uploaded scanner findings: {len(parsed_scan_findings)}"
    )

    if vulnerability_intelligence:
        st.subheader("EPRA Release Risk Intelligence")
        risk_cols = st.columns(4)
        risk_cols[0].metric("Normalized Findings", summary.get("total_findings", 0))
        risk_cols[1].metric("Release Blockers", summary.get("release_blockers", 0))
        risk_cols[2].metric("Critical", summary.get("severity_counts", {}).get("CRITICAL", 0))
        risk_cols[3].metric("High", summary.get("severity_counts", {}).get("HIGH", 0))
        st.caption("Release Risk Score is a 0-100 EPRA score based on severity, CVSS, exploitability, EPSS, package readiness, QA status, and fixed-version evidence.")
        intel_df = pd.DataFrame(vulnerability_intelligence.get("findings", []))
        if not intel_df.empty:
            if "risk_reasons" in intel_df.columns:
                intel_df["risk_reasons"] = intel_df["risk_reasons"].apply(lambda value: "; ".join(value) if isinstance(value, list) else value)
            display_cols = [
                "software_name",
                "cve",
                "severity",
                "release_risk_score",
                "blocker_decision",
                "risk_reasons",
                "package_readiness",
                "qa_result",
                "recommended_action",
            ]
            display_df = intel_df[[col for col in display_cols if col in intel_df.columns]].rename(
                columns={
                    "software_name": "Software",
                    "cve": "CVE",
                    "severity": "Severity",
                    "release_risk_score": "Release Risk Score (0-100)",
                    "blocker_decision": "Decision",
                    "risk_reasons": "Why This Score",
                    "package_readiness": "Package Readiness",
                    "qa_result": "QA Result",
                    "recommended_action": "Recommended Action",
                }
            )
            st.dataframe(ctx.style_operational_table(display_df), use_container_width=True, hide_index=True)
            if package_readiness:
                finding_lookup = {
                    str(row.get("software_name", "")).lower(): row
                    for row in intel_df.to_dict("records")
                    if row.get("software_name")
                }
                coverage_rows = []
                for software, record in package_readiness.items():
                    if not isinstance(record, dict):
                        continue
                    finding = finding_lookup.get(str(software).lower(), {})
                    matched = bool(finding)
                    blocker = str(record.get("Blocker") or "").strip()
                    decision = finding.get("blocker_decision") or "No scanner CVE"
                    risk_score = finding.get("release_risk_score") if matched else "N/A"
                    evidence_status = "Scanner finding mapped" if matched else "No CVE in active scanner evidence"
                    if matched:
                        next_action = finding.get("recommended_action", "Review vulnerability finding with package owner.")
                    elif blocker:
                        next_action = "Continue package readiness blocker resolution; no scanner CVE is mapped to this package."
                    else:
                        next_action = "Continue normal package readiness workflow."
                    coverage_rows.append(
                        {
                            "Software": record.get("Software Name") or software,
                            "Vulnerability Evidence Status": evidence_status,
                            "Release Security Decision": decision,
                            "Risk Score": risk_score,
                            "Package Readiness": record.get("Package Readiness", "Not Assessed"),
                            "Owner": record.get("Owner", "Not Assigned"),
                            "Package Blocker": blocker or "None",
                            "Next Action": next_action,
                        }
                    )
                if coverage_rows:
                    st.subheader("Release Package Vulnerability Coverage")
                    st.caption("Shows release packages mapped against active scanner evidence, so missing CVE evidence is explicit and not confused with a vulnerability finding.")
                    coverage_df = pd.DataFrame(coverage_rows).sort_values(["Release Security Decision", "Software"])
                    st.dataframe(ctx.style_operational_table(coverage_df), use_container_width=True, hide_index=True)

    with st.expander("Advanced: Evidence Ingestion", expanded=False):
        st.info("EPRA can auto-discover scanner reports from the release reports folder. Manual CSV/JSON/XLSX upload is only an optional fallback.")
        st.caption(
            f"EPRA checks `{release_input_dir}` and `{output_dir}` for `reports`, `report`, "
            "`scan_reports`, and `scanner_reports` folders."
        )
        release_reports = evidence_source.get("release_reports", []) or []
        if release_reports:
            st.write("Discovered release report(s):")
            for report in release_reports:
                st.code(report)
        else:
            st.info("No release scanner report was discovered for the selected release.")
        if st.button("Load Release Scanner Report", use_container_width=True, disabled=not release_reports):
            try:
                intelligence, selected = build_from_release_report_if_available(
                    release_input_dir,
                    output_dir,
                    comparison=comparison,
                    package_readiness=package_readiness,
                    qa_validation=qa_validation,
                    release=release,
                )
                ctx.clear_dashboard_cache()
                if selected and intelligence:
                    st.success(f"Loaded release scanner report: {selected.name}")
                    st.rerun()
                else:
                    st.warning("No supported release scanner report was found.")
            except Exception as exc:
                st.error(f"Release scanner report was not loaded: {exc}")

        st.divider()
        st.caption("Upload scanner CSV/JSON/XLSX report, or place reports under the release reports folder for auto-discovery.")
        scan_file = st.file_uploader("Upload Vulnerability Scan Report", type=["json", "csv", "xlsx", "xls"], key="vulnerability_scan_report_upload")
        if st.button("Parse Scan Report", use_container_width=True, disabled=scan_file is None):
            try:
                saved_path = save_uploaded_scan_report(output_dir, scan_file)
                uploaded_findings = parse_scan_report(saved_path)
                save_parsed_scan_findings(output_dir, uploaded_findings)
                build_and_save_vulnerability_intelligence(
                    output_dir,
                    uploaded_findings,
                    comparison=comparison,
                    package_readiness=package_readiness,
                    qa_validation=qa_validation,
                    release=release,
                )
                ctx.clear_dashboard_cache()
                st.success(f"Parsed {len(uploaded_findings)} scanner finding(s) from {saved_path.name}.")
                st.rerun()
            except Exception as exc:
                st.error(f"Scan report was not parsed: {exc}")

    if parsed_scan_findings:
        with st.expander("Advanced: Uploaded Scanner Findings", expanded=False):
            scan_df = pd.DataFrame(parsed_scan_findings)
            scan_cols = [col for col in ["Software Name", "Version", "CVE", "Severity", "Risk Level", "Scanner Source", "Source File", "Parsed At"] if col in scan_df.columns]
            st.dataframe(ctx.style_operational_table(scan_df[scan_cols]), use_container_width=True, hide_index=True)

    if vuln_df.empty:
        st.info("No NVD vulnerability data found. Upload a scanner report or run the vulnerability workflow.")
        return
    with st.expander("Advanced: NVD Fallback Details", expanded=False):
        risk_counts = vuln_df["Risk Level"].value_counts().to_dict()
        cols = st.columns(5)
        for col, risk in zip(cols, ["CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE"]):
            col.metric(f"{risk.title()} Risk", risk_counts.get(risk, 0))

        assessment_cols = [
            "Software Name",
            "Current Installed Version",
            "Latest Available Version",
            "Version Assessed",
            "CVE Severity",
            "Risk Level",
            "Security Assessment",
            "Source",
        ]
        ctx.searchable_table(vuln_df[assessment_cols], "vulnerability_assessment", ["Risk Level", "CVE Severity", "Version Assessed", "Source"])

        left, right = st.columns(2)
        with left:
            heatmap_df = vuln_df[["Software Name", "Risk Level", "CVE Count"]].copy()
            heatmap_df["Risk Score"] = heatmap_df["Risk Level"].map({"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}).fillna(0)
            chart = (
                alt.Chart(heatmap_df)
                .mark_rect()
                .encode(
                    x=alt.X("Software Name:N", title=None),
                    y=alt.Y("Risk Level:N", title=None, sort=RISK_ORDER),
                    color=alt.Color("Risk Score:Q", scale=alt.Scale(range=["#1e293b", "#22c55e", "#f59e0b", "#f97316", "#ef4444"])),
                    tooltip=list(heatmap_df.columns),
                )
                .properties(height=280, title="Risk Heatmap")
            )
            st.altair_chart(chart, use_container_width=True)
        with right:
            severity_df = vuln_df["CVE Severity"].value_counts().reset_index()
            severity_df.columns = ["Severity", "Count"]
            ctx.donut_chart(severity_df, "Severity", "Count", "Severity Distribution")

        st.subheader("Security Review Queue")
        top = vuln_df.sort_values(["CVE Count", "Risk Level"], ascending=[False, True]).head(10)
        st.dataframe(ctx.style_operational_table(top[["Software Name", "Risk Level", "CVE Severity", "CVE Count", "Security Assessment"]]), use_container_width=True, hide_index=True)

        posture_score = max(0, 100 - (risk_counts.get("CRITICAL", 0) * 30) - (risk_counts.get("HIGH", 0) * 20) - (risk_counts.get("MEDIUM", 0) * 10))
        st.progress(posture_score / 100, text=f"Security Posture Gauge: {posture_score}%")


