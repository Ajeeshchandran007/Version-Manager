from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from App.auth import (
    ROLE_ADMIN,
    ROLE_QA_ENGINEER,
    ROLE_RELEASE_ENGINEER,
    current_role,
)
from App.workspace import (
    active_output_path,
)


ACTION_ROLES = {ROLE_ADMIN, ROLE_RELEASE_ENGINEER, ROLE_QA_ENGINEER}
ADMIN_ROLES = {ROLE_ADMIN}
RISK_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE", "UNKNOWN"]
PRIMARY_CACHE_NAMESPACES = {"software_versions", "vulnerabilities", "nvd"}
CACHE_NAMESPACE_LABELS = {
    "software_versions": "Software Versions",
    "vulnerabilities": "Vulnerability Assessments",
    "nvd": "NVD CVE Lookup",
    "tavily": "Tavily Search",
    "openai_analysis": "OpenAI Analysis",
    "vendor_sources": "Vendor Sources",
}

def render_reports(current_df: pd.DataFrame, comparison_df: pd.DataFrame, vuln_df: pd.DataFrame, ctx: Any) -> None:
    ctx.section_title("Reports", "Management and technical deliverables for review, download, and email distribution.")
    col1, col2, col3 = st.columns(3)
    col1.metric("Applications", len(current_df))
    col2.metric("Updates Required", int((comparison_df["Need Update"] == "Yes").sum()) if not comparison_df.empty else 0)
    col3.metric("Security Findings", int(vuln_df["CVE Count"].sum()) if not vuln_df.empty else 0)

    st.subheader("Report Package")
    email_html_file = active_output_path("email_preview.html")
    mime_by_name = {
        "email_preview.html": "text/html",
        "Software_Version_Assessment.xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Test_Case_Impact_Assessment.xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "package_readiness.json": "application/json",
        "qa_validation.json": "application/json",
        "testcase_impact.json": "application/json",
    }
    files = [
        (label, path, mime_by_name.get(path.name, "application/octet-stream"))
        for label, path in ctx.visible_output_files_for_role(current_role(), include_operational_reports=False)
    ]
    cols = st.columns(min(len(files), 4))
    for col, (label, path, mime) in zip(cols, files):
        with col:
            st.markdown(f"**{label}**")
            if "Technical" in label:
                st.caption("Detailed versions, CVE severity, risk, recommendations, and scan evidence.")
            elif "Package" in label:
                st.caption("Release engineering readiness, checklist, owner, installer type, and blockers.")
            elif "QA" in label:
                st.caption("Compatibility, installation validation, functional checks, and QA test notes.")
            elif "Test Case" in label:
                st.caption("Recommended QA regression and validation test cases for software requiring updates.")
            else:
                st.caption("Executive summary for managers, stakeholders, and email distribution.")
            st.caption(path.name)
            if path.exists():
                st.download_button(f"Download {label}", path.read_bytes(), file_name=path.name, mime=mime, use_container_width=True)
            else:
                st.warning("Not available")

    st.subheader("Management Report Preview")
    st.caption("This is the business-focused report body used for email notifications.")
    html = ctx.load_file_text(str(email_html_file), ctx.file_mtime(email_html_file))
    if html:
        components.html(html, height=760, scrolling=True)
    else:
        st.info("No HTML email preview found.")
