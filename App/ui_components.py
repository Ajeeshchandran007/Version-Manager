from __future__ import annotations

from typing import Any

import altair as alt
import pandas as pd
import streamlit as st


def style_operational_table(df: pd.DataFrame) -> Any:
    if df.empty:
        return df

    def color_cell(value: Any) -> str:
        text = str(value).upper()
        if text in {"CRITICAL", "HIGH", "YES", "OUTDATED"}:
            return "background-color: #FCE8E6; color: #8A1C12; font-weight: 700; border: 1px solid #F4B8B2;"
        if text in {"MEDIUM", "MAJOR GAP", "CU GAP"}:
            return "background-color: #FFF4D6; color: #7A4B00; font-weight: 700; border: 1px solid #F3D58A;"
        if text in {"LOW", "MINOR GAP"}:
            return "background-color: #E8F2FF; color: #174A7C; font-weight: 700; border: 1px solid #BBD7F2;"
        if text in {"NO", "UP-TO-DATE", "NONE", "ACTIVE"}:
            return "background-color: #E7F6ED; color: #135D31; font-weight: 700; border: 1px solid #B9E3C8;"
        if text in {"INACTIVE - LOGIN DISABLED"}:
            return "background-color: #F3F4F6; color: #4B5563; font-weight: 700; border: 1px solid #D1D5DB;"
        return ""

    return (
        df.style
        .map(color_cell)
        .set_properties(**{"color": "#1F2937", "background-color": "#FFFFFF"})
        .set_table_styles(
            [
                {
                    "selector": "th",
                    "props": [
                        ("background-color", "#F3F6FA"),
                        ("color", "#1F2937"),
                        ("font-weight", "700"),
                        ("border-bottom", "1px solid #D8DEE8"),
                    ],
                },
                {
                    "selector": "td",
                    "props": [
                        ("border-bottom", "1px solid #E5EAF0"),
                    ],
                },
            ]
        )
    )


def readable_cell_class(value: Any) -> str:
    text = str(value).upper()
    if text in {"CRITICAL", "HIGH", "YES", "OUTDATED"}:
        return "vm-cell-red"
    if text in {"MEDIUM", "MAJOR GAP", "CU GAP"}:
        return "vm-cell-amber"
    if text in {"LOW", "MINOR GAP"}:
        return "vm-cell-blue"
    if text in {"NO", "UP-TO-DATE", "NONE"}:
        return "vm-cell-green"
    return ""


def render_readable_table(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("No records found. Run the pipeline to generate the required output files.")
        return
    headers = "".join(f"<th>{column}</th>" for column in df.columns)
    rows = []
    for _, row in df.iterrows():
        cells = []
        for column in df.columns:
            value = row[column]
            css_class = readable_cell_class(value)
            cells.append(f"<td class='{css_class}'>{value}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    st.markdown(
        f"""
        <div class="vm-table-wrap">
            <table class="vm-readable-table">
                <thead><tr>{headers}</tr></thead>
                <tbody>{''.join(rows)}</tbody>
            </table>
        </div>
        """,
        unsafe_allow_html=True,
    )


def bar_chart(df: pd.DataFrame, x: str, y: str, title: str, color_field: str | None = None) -> None:
    if df.empty:
        st.info(f"No data available for {title}.")
        return
    chart = (
        alt.Chart(df)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X(f"{x}:N", sort="-y", axis=alt.Axis(labelAngle=-25)),
            y=alt.Y(f"{y}:Q"),
            tooltip=list(df.columns),
        )
        .properties(height=300, title=title)
        .configure_view(stroke=None)
        .configure_axis(labelColor="#1f2937", titleColor="#1f2937", gridColor="#e5e7eb")
        .configure_title(color="#111827", fontSize=14, anchor="start", dy=-4)
    )
    if color_field:
        chart = chart.encode(
            color=alt.Color(
                f"{color_field}:N",
                legend=alt.Legend(orient="bottom", labelLimit=220),
                scale=alt.Scale(range=["#38bdf8", "#22c55e", "#f59e0b", "#f97316", "#ef4444"]),
            )
        )
    st.altair_chart(chart, use_container_width=True)


def donut_chart(df: pd.DataFrame, category: str, value_col: str, title: str) -> None:
    if df.empty:
        st.info(f"No data available for {title}.")
        return
    chart = (
        alt.Chart(df)
        .mark_arc(innerRadius=65, outerRadius=110)
        .encode(
            theta=alt.Theta(f"{value_col}:Q"),
            color=alt.Color(
                f"{category}:N",
                legend=alt.Legend(orient="bottom", labelLimit=220),
                scale=alt.Scale(range=["#22c55e", "#f59e0b", "#f97316", "#ef4444", "#38bdf8", "#94a3b8"]),
            ),
            tooltip=[category, value_col],
        )
        .properties(height=280, title=title)
        .configure_view(stroke=None)
        .configure_axis(labelColor="#1f2937", titleColor="#1f2937")
        .configure_title(color="#111827", fontSize=14, anchor="start", dy=-4)
    )
    st.altair_chart(chart, use_container_width=True)


def searchable_table(df: pd.DataFrame, key: str, filter_columns: list[str] | None = None) -> pd.DataFrame:
    if df.empty:
        st.info("No records found. Run the pipeline to generate the required output files.")
        return df
    query = st.text_input("Search", key=f"{key}_search", placeholder="Search software, vendor, version, status")
    filtered = df.copy()
    if query:
        mask = filtered.astype(str).apply(lambda row: row.str.contains(query, case=False, na=False).any(), axis=1)
        filtered = filtered[mask]
    if filter_columns:
        cols = st.columns(len(filter_columns))
        for col, field in zip(cols, filter_columns):
            values = ["All"] + sorted([str(item) for item in filtered[field].dropna().unique().tolist()])
            selected = col.selectbox(field, values, key=f"{key}_{field}")
            if selected != "All":
                filtered = filtered[filtered[field].astype(str) == selected]
    render_readable_table(filtered)
    st.download_button(
        "Export CSV",
        filtered.to_csv(index=False).encode("utf-8"),
        file_name=f"{key}.csv",
        mime="text/csv",
        use_container_width=False,
    )
    return filtered
