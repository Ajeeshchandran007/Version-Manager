"""Structured observability helpers for agent and MCP runs."""
import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from Utils.utils import logger

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_METRICS_PATH = _PROJECT_ROOT / "output" / "metrics.jsonl"


def new_trace_id(prefix: str = "trace") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def emit_event(event: str, trace_id: str, **fields: Any) -> None:
    payload = {
        "event": event,
        "trace_id": trace_id,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **fields,
    }
    logger.info(json.dumps(payload, default=str))


def emit_metric(name: str, value: float, trace_id: str, **labels: Any) -> None:
    record = {
        "metric": name,
        "value": value,
        "trace_id": trace_id,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "labels": labels,
    }
    _METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_METRICS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + os.linesep)


@contextmanager
def observed_step(step: str, trace_id: str, **labels: Any):
    start = time.perf_counter()
    emit_event(f"{step}.started", trace_id, **labels)
    try:
        yield
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        emit_metric(f"{step}.duration_ms", elapsed_ms, trace_id, status="error", **labels)
        emit_event(f"{step}.failed", trace_id, error=str(exc), **labels)
        raise
    else:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        emit_metric(f"{step}.duration_ms", elapsed_ms, trace_id, status="ok", **labels)
        emit_event(f"{step}.completed", trace_id, **labels)
