from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

PRIMARY_CACHE_NAMESPACES = {"software_versions", "vulnerabilities", "nvd"}
CACHE_NAMESPACE_LABELS = {
    "software_versions": "Software Versions",
    "vulnerabilities": "Vulnerability Assessments",
    "nvd": "NVD CVE Lookup",
    "tavily": "Tavily Search",
    "openai_analysis": "OpenAI Analysis",
    "vendor_sources": "Vendor Sources",
}


def render_cache(cache_metrics: dict[str, Any], ctx: Any) -> None:
    ctx.section_title("Cache Analytics", "Operational cache utilization, API reduction, and token savings.")
    namespaces = {key: val for key, val in cache_metrics.items() if isinstance(val, dict)}
    rows = []
    for namespace, record in namespaces.items():
        hits = int(record.get("hits", 0))
        misses = int(record.get("misses", 0))
        total = hits + misses
        rows.append(
            {
                "Namespace": namespace,
                "Cache Area": CACHE_NAMESPACE_LABELS.get(namespace, namespace.replace("_", " ").title()),
                "Cache Hits": hits,
                "Cache Misses": misses,
                "Hit Ratio": round((hits / total) * 100, 1) if total else 0,
                "API Calls Saved": int(record.get("estimated_api_calls_saved", 0)),
                "Estimated Token Savings": int(record.get("estimated_tokens_saved", 0)),
                "Bypasses": int(record.get("bypasses", 0)),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        st.info("No cache metrics available.")
        return
    primary_df = df[df["Namespace"].isin(PRIMARY_CACHE_NAMESPACES)].copy()
    advanced_df = df[~df["Namespace"].isin(PRIMARY_CACHE_NAMESPACES)].copy()
    if primary_df.empty:
        primary_df = df.copy()

    totals = primary_df[["Cache Hits", "Cache Misses", "API Calls Saved", "Estimated Token Savings"]].sum()
    cols = st.columns(5)
    cols[0].metric("Cache Hits", int(totals["Cache Hits"]))
    cols[1].metric("Cache Misses", int(totals["Cache Misses"]))
    total_requests = totals["Cache Hits"] + totals["Cache Misses"]
    cols[2].metric("Hit Ratio", f"{round((totals['Cache Hits'] / total_requests) * 100, 1) if total_requests else 0}%")
    cols[3].metric("API Calls Saved", int(totals["API Calls Saved"]))
    cols[4].metric("Token Savings", int(totals["Estimated Token Savings"]))

    display_cols = ["Cache Area", "Cache Hits", "Cache Misses", "Hit Ratio", "API Calls Saved", "Estimated Token Savings", "Bypasses"]
    st.dataframe(primary_df[display_cols], use_container_width=True, hide_index=True)
    left, right = st.columns(2)
    with left:
        ctx.bar_chart(primary_df, "Cache Area", "Hit Ratio", "Cache Efficiency", "Cache Area")
    with right:
        ctx.bar_chart(primary_df, "Cache Area", "API Calls Saved", "API Calls Avoided", "Cache Area")

    if not advanced_df.empty:
        with st.expander("Advanced cache details"):
            st.caption("Internal cache layers used for troubleshooting vendor search, LLM parsing, and direct source fetches.")
            st.dataframe(advanced_df[display_cols], use_container_width=True, hide_index=True)
