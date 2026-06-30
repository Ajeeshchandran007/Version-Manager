"""Retry and circuit-breaker primitives for unreliable external calls."""
import asyncio
import time
from collections import defaultdict
from typing import Awaitable, Callable, TypeVar

from Utils.utils import logger

T = TypeVar("T")

_failures: dict[str, list[float]] = defaultdict(list)


class CircuitOpenError(RuntimeError):
    pass


def _circuit_open(key: str, threshold: int, window_seconds: int) -> bool:
    now = time.time()
    recent = [ts for ts in _failures[key] if now - ts <= window_seconds]
    _failures[key] = recent
    return len(recent) >= threshold


def _record_failure(key: str) -> None:
    _failures[key].append(time.time())


async def with_retries(
    operation: Callable[[], Awaitable[T]],
    *,
    operation_name: str,
    attempts: int = 3,
    base_delay_seconds: float = 1.0,
    circuit_threshold: int = 5,
    circuit_window_seconds: int = 300,
) -> T:
    if _circuit_open(operation_name, circuit_threshold, circuit_window_seconds):
        raise CircuitOpenError(f"Circuit open for {operation_name}")

    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except Exception as exc:
            last_exc = exc
            _record_failure(operation_name)
            logger.warning(
                "Operation failed: %s attempt=%s/%s error=%s",
                operation_name,
                attempt,
                attempts,
                exc,
            )
            if attempt < attempts:
                await asyncio.sleep(base_delay_seconds * attempt)

    raise last_exc or RuntimeError(f"Operation failed: {operation_name}")
