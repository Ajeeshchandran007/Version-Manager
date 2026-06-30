"""Maps software updates to recommended QA test cases."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pandas as pd

from Core.notifier import is_actionable_update
from Utils.utils import logger


REQUIRED_COLUMNS = [
    "Software Name",
    "Vendor",
    "Module / Component",
    "Test Case ID",
    "Test Case Name",
    "Update Scenario",
    "Test Type",
    "Priority",
    "Automation Status",
    "Owner",
    "Applicable Version",
    "Precondition",
    "Expected Result",
]


def build_testcase_impact(
    comparison: dict[str, Any],
    repository_path: str,
) -> dict[str, Any]:
    """Return impacted QA test cases for software that requires updates."""
    repository = load_testcase_repository(repository_path)
    impacted: dict[str, Any] = {}
    flat_rows: list[dict[str, Any]] = []

    for software_name, result in comparison.items():
        if not is_actionable_update(result):
            continue

        current = result.get("current") or {}
        latest = result.get("latest") or {}
        current_version = _version_label(current)
        target_version = _version_label(latest)
        matches = _match_testcases(repository, software_name)
        rows = [_row_to_dict(row) for _, row in matches.iterrows()]
        priority = _recommended_priority(rows)
        coverage = "Repository" if rows else "Not Found"
        recommendation = _recommendation_text(software_name, rows)

        impacted[software_name] = {
            "Software Name": software_name,
            "Current Version": current_version,
            "Target Version": target_version,
            "Test Coverage": coverage,
            "Test Case Count": len(rows),
            "Recommended Priority": priority,
            "Recommended Test Cases": rows,
            "Recommendation": recommendation,
        }

        if rows:
            for row in rows:
                flat_rows.append({
                    "Software Name": software_name,
                    "Current Version": current_version,
                    "Target Version": target_version,
                    "Test Coverage": coverage,
                    "Recommended Priority": priority,
                    **row,
                })

    return {
        "summary": {
            "software_requiring_update": len(impacted),
            "software_with_test_coverage": sum(1 for item in impacted.values() if item["Test Case Count"] > 0),
            "software_without_test_coverage": sum(1 for item in impacted.values() if item["Test Case Count"] == 0),
            "total_recommended_test_cases": sum(item["Test Case Count"] for item in impacted.values()),
            "repository_path": str(repository_path),
        },
        "impacted_software": impacted,
        "test_case_plan": flat_rows,
    }


def save_testcase_impact_outputs(
    comparison: dict[str, Any],
    repository_path: str,
    json_path: str,
    excel_path: str | None = None,
) -> dict[str, Any]:
    impact = build_testcase_impact(comparison, repository_path)
    _write_json(json_path, impact)
    if excel_path:
        write_testcase_plan_excel(impact, excel_path)
    return impact


def load_testcase_repository(repository_path: str) -> pd.DataFrame:
    path = Path(repository_path)
    if not path.exists():
        logger.warning("Test case repository not found: %s", path)
        return pd.DataFrame(columns=REQUIRED_COLUMNS)

    df = pd.read_excel(path)
    for column in REQUIRED_COLUMNS:
        if column not in df.columns:
            df[column] = ""
    return df[REQUIRED_COLUMNS].fillna("")


def write_testcase_plan_excel(impact: dict[str, Any], excel_path: str) -> None:
    rows = impact.get("test_case_plan") or []
    path = Path(excel_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, index=False, sheet_name="Recommended Test Cases")
        summary = impact.get("summary", {})
        pd.DataFrame([summary]).to_excel(writer, index=False, sheet_name="Summary")


def _match_testcases(repository: pd.DataFrame, software_name: str) -> pd.DataFrame:
    if repository.empty:
        return repository
    needle = _normalize_name(software_name)
    normalized = repository["Software Name"].astype(str).map(_normalize_name)
    exact = repository[normalized == needle]
    if not exact.empty:
        return exact.sort_values(by=["Priority", "Test Case ID"], key=_sort_key)

    # Conservative fallback for naming variants, such as "Exchange" vs "MS Exchange Server 2019".
    tokens = [token for token in re.split(r"[^a-z0-9]+", needle) if len(token) >= 4]
    if not tokens:
        return repository.iloc[0:0]
    mask = normalized.apply(lambda value: any(token in value for token in tokens))
    return repository[mask].sort_values(by=["Priority", "Test Case ID"], key=_sort_key)


def _sort_key(series: pd.Series) -> pd.Series:
    priority_rank = {"Critical": "0", "High": "1", "Medium": "2", "Low": "3"}
    return series.astype(str).map(lambda value: priority_rank.get(value, value))


def _row_to_dict(row: pd.Series) -> dict[str, str]:
    return {column: str(row.get(column, "")).strip() for column in REQUIRED_COLUMNS}


def _version_label(record: dict[str, Any]) -> str:
    build = str(record.get("Build Version") or record.get("version") or "").strip()
    cu = str(record.get("Cumulative Update (CU)") or record.get("cu") or "").strip()
    return f"{build} ({cu})" if build and cu else build


def _recommended_priority(rows: list[dict[str, str]]) -> str:
    priorities = {row.get("Priority", "").title() for row in rows}
    if "Critical" in priorities:
        return "Critical"
    if "High" in priorities:
        return "High"
    if "Medium" in priorities:
        return "Medium"
    if rows:
        return "Low"
    return "High"


def _recommendation_text(software_name: str, rows: list[dict[str, str]]) -> str:
    if rows:
        return f"Run {len(rows)} mapped QA test case(s) from the repository before approving {software_name} upgrade."
    return f"No mapped QA test cases found for {software_name}. Add this software to the test case repository if validation is required."


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def _write_json(path: str, payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
