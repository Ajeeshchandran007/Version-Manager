from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from App.formatting import format_duration_ms, format_ts


def friendly_event(metric: str) -> tuple[str, str, str]:
    mapping = {
        "fetch_latest_versions.duration_ms": (
            "Latest versions refreshed",
            "The system checked the latest approved vendor versions.",
            "Version Catalog",
        ),
        "fetch_current_versions.duration_ms": (
            "Current inventory refreshed",
            "The system collected installed versions from servers or fallback documents.",
            "Inventory",
        ),
        "compare_versions.duration_ms": (
            "Version comparison completed",
            "Installed versions were compared against the latest-version catalog.",
            "Compliance",
        ),
        "check_vulnerabilities.duration_ms": (
            "Vulnerability assessment completed",
            "Current installed versions were checked against vulnerability data.",
            "Security",
        ),
        "send_notification.duration_ms": (
            "Email notification processed",
            "The version assessment report email was generated and sent using configured mail settings.",
            "Notification",
        ),
    }
    return mapping.get(metric, ("Workflow event recorded", "A system workflow event was recorded.", "System"))


def build_audit_events(metrics_df: pd.DataFrame, cache_metrics: dict[str, Any]) -> pd.DataFrame:
    rows = []
    if not metrics_df.empty:
        for _, item in metrics_df.tail(100).iterrows():
            labels = item.get("labels", {})
            if not isinstance(labels, dict):
                labels = {}
            title, description, category = friendly_event(str(item.get("metric", "")))
            rows.append(
                {
                    "Timestamp": format_ts(str(item.get("ts", ""))),
                    "Category": category,
                    "Activity": title,
                    "Status": str(labels.get("status", "ok")).title(),
                    "Duration": format_duration_ms(item.get("value")),
                    "Details": description,
                    "Trace ID": item.get("trace_id", ""),
                    "Technical Event": item.get("metric", ""),
                }
            )

    cache_updated = format_ts(cache_metrics.get("last_updated")) if cache_metrics else "Not available"
    for namespace, record in cache_metrics.items():
        if isinstance(record, dict):
            hits = int(record.get("hits", 0))
            misses = int(record.get("misses", 0))
            saved = int(record.get("estimated_api_calls_saved", 0))
            rows.append(
                {
                    "Timestamp": cache_updated,
                    "Category": "Cache",
                    "Activity": f"{namespace.replace('_', ' ').title()} cache updated",
                    "Status": "Ok",
                    "Duration": "Not applicable",
                    "Details": f"{hits} cache hits, {misses} misses, {saved} API calls saved.",
                    "Trace ID": "",
                    "Technical Event": namespace,
                }
            )
    return pd.DataFrame(rows)


def render_audit(metrics_df: pd.DataFrame, cache_metrics: dict[str, Any], ctx: Any) -> None:
    ctx.section_title("Activity History", "Readable operational history for scans, reports, cache usage, and notifications.")
    df = build_audit_events(metrics_df, cache_metrics)
    if df.empty:
        st.info("No activity history is available yet. Run the pipeline to create workflow events.")
        return

    total_events = len(df)
    successful = int((df["Status"].str.upper() == "OK").sum())
    cache_events = int((df["Category"] == "Cache").sum())
    last_event = df["Timestamp"].iloc[-1] if not df.empty else "Not available"
    cols = st.columns(4)
    cols[0].metric("Activities Recorded", total_events)
    cols[1].metric("Successful Events", successful)
    cols[2].metric("Cache Events", cache_events)
    cols[3].metric("Latest Activity", last_event)

    display_cols = ["Timestamp", "Category", "Activity", "Status", "Duration", "Details"]
    ctx.searchable_table(df[display_cols], "activity_history", ["Category", "Status"])

    with st.expander("Technical audit details"):
        technical_cols = ["Timestamp", "Technical Event", "Trace ID", "Status", "Duration"]
        st.dataframe(df[technical_cols], use_container_width=True, hide_index=True)
