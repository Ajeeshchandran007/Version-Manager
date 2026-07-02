from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

HISTORY_FILE = "qa_validation_history.json"


def _safe_int(raw: Any) -> int:
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return 0


def _coverage(executed: int, total: int) -> float:
    return round((executed / total) * 100, 1) if total else 0.0


def calculate_qa_summary(qa_df: pd.DataFrame) -> dict[str, Any]:
    if qa_df.empty:
        return {
            "total_software": 0,
            "total_test_cases": 0,
            "executed_test_cases": 0,
            "coverage_percent": 0.0,
            "fully_tested": 0,
            "partially_tested": 0,
            "not_tested": 0,
            "pass": 0,
            "fail": 0,
            "warning": 0,
        }

    total_cases = int(qa_df.get("Test Case Count", pd.Series(dtype=int)).map(_safe_int).sum())
    executed_cases = int(qa_df.get("Test Cases Executed", pd.Series(dtype=int)).map(_safe_int).sum())
    result_counts = qa_df.get("Test Result", pd.Series(dtype=str)).astype(str).str.upper().value_counts().to_dict()

    counts = qa_df.apply(
        lambda row: (
            _safe_int(row.get("Test Case Count")),
            _safe_int(row.get("Test Cases Executed")),
        ),
        axis=1,
    )
    fully_tested = sum(1 for total, executed in counts if total > 0 and executed >= total)
    partially_tested = sum(1 for total, executed in counts if total > 0 and 0 < executed < total)
    not_tested = sum(1 for total, executed in counts if total == 0 or executed == 0)

    return {
        "total_software": int(len(qa_df)),
        "total_test_cases": total_cases,
        "executed_test_cases": min(executed_cases, total_cases) if total_cases else executed_cases,
        "coverage_percent": _coverage(min(executed_cases, total_cases), total_cases),
        "fully_tested": int(fully_tested),
        "partially_tested": int(partially_tested),
        "not_tested": int(not_tested),
        "pass": int(result_counts.get("PASS", 0)),
        "fail": int(result_counts.get("FAIL", 0)),
        "warning": int(result_counts.get("WARNING", 0)),
    }


def history_path(output_dir: Path) -> Path:
    return output_dir / HISTORY_FILE


def load_qa_history(output_dir: Path) -> list[dict[str, Any]]:
    path = history_path(output_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def append_qa_history(output_dir: Path, record: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = load_qa_history(output_dir)
    rows.append({"timestamp": datetime.now().isoformat(timespec="seconds"), **record})
    history_path(output_dir).write_text(json.dumps(rows, indent=2), encoding="utf-8")


def build_qa_update_history_record(
    product: str,
    release_line: str,
    software_name: str,
    installation_status: str,
    test_result: str,
    test_case_count: int,
    test_cases_executed: int,
    tested_by: str,
    notes: str,
) -> dict[str, Any]:
    executed = min(_safe_int(test_cases_executed), _safe_int(test_case_count)) if _safe_int(test_case_count) else _safe_int(test_cases_executed)
    total = _safe_int(test_case_count)
    return {
        "event_type": "QA Update",
        "product": product,
        "release_line": release_line,
        "software": software_name,
        "installation_status": installation_status,
        "test_result": test_result,
        "test_case_count": total,
        "test_cases_executed": executed,
        "coverage_percent": _coverage(executed, total),
        "tested_by": tested_by,
        "notes": notes.strip(),
    }


def history_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)
