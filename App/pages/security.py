from __future__ import annotations

from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

from App.scan_reports import (
    load_parsed_scan_findings,
    parse_scan_report,
    save_parsed_scan_findings,
    save_uploaded_scan_report,
)
from App.workspace import active_output_path


RISK_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE", "UNKNOWN"]


def render_vulnerabilities(vuln_df: pd.DataFrame, ctx: Any) -> None:
    ctx.section_title("Vulnerability Assessment", "Security assessment of current installed versions with latest version context.")
    output_dir = active_output_path("__placeholder__").parent
    parsed_scan_findings = load_parsed_scan_findings(output_dir)
    st.subheader("Vulnerability Data Source")
    source_cols = st.columns(3)
    source_cols[0].metric("NVD Lookup", "Available" if not vuln_df.empty else "Not Available")
    source_cols[1].metric("Uploaded Scan Findings", len(parsed_scan_findings))
    source_cols[2].metric("Active Display", "NVD Report" if not vuln_df.empty else "Uploaded Scan Report" if parsed_scan_findings else "No Data")
    with st.expander("Upload Scanner Report", expanded=False):
        st.caption("Supported first-pass formats: JSON, CSV, XLSX, XLS. If no scanner report is available, the page continues to use NVD lookup results.")
        scan_file = st.file_uploader("Upload Vulnerability Scan Report", type=["json", "csv", "xlsx", "xls"], key="vulnerability_scan_report_upload")
        if st.button("Parse Scan Report", use_container_width=True, disabled=scan_file is None):
            try:
                saved_path = save_uploaded_scan_report(output_dir, scan_file)
                findings = parse_scan_report(saved_path)
                save_parsed_scan_findings(output_dir, findings)
                ctx.clear_dashboard_cache()
                st.success(f"Parsed {len(findings)} scanner finding(s) from {saved_path.name}.")
                st.rerun()
            except Exception as exc:
                st.error(f"Scan report was not parsed: {exc}")

    if parsed_scan_findings:
        st.subheader("Uploaded Scanner Findings")
        scan_df = pd.DataFrame(parsed_scan_findings)
        scan_cols = [col for col in ["Software Name", "Version", "CVE", "Severity", "Risk Level", "Scanner Source", "Source File", "Parsed At"] if col in scan_df.columns]
        st.dataframe(ctx.style_operational_table(scan_df[scan_cols]), use_container_width=True, hide_index=True)

    if vuln_df.empty:
        st.info("No NVD vulnerability data found. Upload a scanner report or run the vulnerability workflow.")
        return
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


