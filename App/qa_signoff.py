from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from App.qa_history import calculate_qa_summary

SIGNOFF_FILE = "qa_signoff.json"


def signoff_path(output_dir: Path) -> Path:
    return output_dir / SIGNOFF_FILE


def load_qa_signoff(output_dir: Path) -> dict[str, Any]:
    path = signoff_path(output_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def build_qa_signoff(product: str, release_line: str, qa_df, signed_by: str, comments: str) -> dict[str, Any]:
    summary = calculate_qa_summary(qa_df)
    status = "QA Signed Off"
    if summary["fail"]:
        status = "QA Signed Off With Failures"
    elif summary["warning"] or summary["not_tested"]:
        status = "QA Signed Off With Warnings"

    return {
        "product": product,
        "release_line": release_line,
        "status": status,
        "signed_by": signed_by.strip() or "unknown",
        "signed_date": datetime.now().isoformat(timespec="seconds"),
        "comments": comments.strip(),
        **summary,
    }


def save_qa_signoff(output_dir: Path, signoff: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    signoff_path(output_dir).write_text(json.dumps(signoff, indent=2), encoding="utf-8")
