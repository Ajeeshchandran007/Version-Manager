from __future__ import annotations

from datetime import datetime
from typing import Any


def parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def format_ts(raw: str | None) -> str:
    parsed = parse_ts(raw)
    if not parsed:
        return "Not available"
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def format_epoch_ts(raw: float) -> str:
    if not raw:
        return "Not available"
    return datetime.fromtimestamp(raw).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def format_duration_ms(raw: Any) -> str:
    try:
        value_ms = float(raw)
    except (TypeError, ValueError):
        return "Not available"
    if value_ms < 1000:
        return f"{value_ms:.0f} ms"
    return f"{value_ms / 1000:.1f} sec"
