"""Enterprise cache helpers for external version/security lookups."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from Utils.utils import logger

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CacheBackend(Protocol):
    def get(self, namespace: str, key: str, ttl_seconds: int) -> dict[str, Any] | None:
        ...

    def set(
        self,
        namespace: str,
        key: str,
        value: Any,
        source: str,
        savings: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        ...


class JsonCacheBackend:
    """Thread-safe JSON file cache backend."""

    def __init__(self, cache_dir: str = "output/cache"):
        path = Path(cache_dir)
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
        self.cache_dir = path
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def get(self, namespace: str, key: str, ttl_seconds: int) -> dict[str, Any] | None:
        path = self._path(namespace, key)
        with self._lock:
            if not path.exists():
                return None
            try:
                entry = json.loads(path.read_text())
            except Exception as exc:
                logger.warning("Cache read failed for %s:%s: %s", namespace, key, exc)
                return None

        cached_at = _parse_ts(entry.get("cached_at"))
        if not cached_at:
            return None
        expires_at = cached_at + timedelta(seconds=ttl_seconds)
        if _now() >= expires_at:
            return None

        entry["cache"] = {
            "status": "hit",
            "namespace": namespace,
            "key": key,
            "cached_at": entry.get("cached_at"),
            "expires_at": expires_at.isoformat(),
            "source": entry.get("source"),
            "savings": entry.get("savings", {}),
        }
        return entry

    def set(
        self,
        namespace: str,
        key: str,
        value: Any,
        source: str,
        savings: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        entry = {
            "cached_at": _now().isoformat(),
            "source": source,
            "value": value,
            "savings": savings or {},
        }
        path = self._path(namespace, key)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(entry, indent=2))
            os.replace(tmp, path)
        return entry

    def _path(self, namespace: str, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.cache_dir / namespace / f"{digest}.json"


class CacheManager:
    """Facade with TTL policy, metadata, and hit/miss metrics."""

    def __init__(self, config: dict):
        cache_cfg = config.get("cache", {})
        self.enabled = bool(cache_cfg.get("enabled", True))
        self.ttls = {
            "software_versions": int(cache_cfg.get("software_versions_ttl_seconds", 7 * 24 * 60 * 60)),
            "vulnerabilities": int(cache_cfg.get("vulnerabilities_ttl_seconds", 24 * 60 * 60)),
            "tavily": int(cache_cfg.get("software_versions_ttl_seconds", 7 * 24 * 60 * 60)),
            "openai_analysis": int(cache_cfg.get("software_versions_ttl_seconds", 7 * 24 * 60 * 60)),
            "vendor_sources": int(cache_cfg.get("software_versions_ttl_seconds", 7 * 24 * 60 * 60)),
            "nvd": int(cache_cfg.get("vulnerabilities_ttl_seconds", 24 * 60 * 60)),
        }
        backend = cache_cfg.get("backend", "json").lower()
        if backend != "json":
            logger.warning("Cache backend '%s' not implemented; using JSON backend.", backend)
        self.backend: CacheBackend = JsonCacheBackend(cache_cfg.get("directory", "output/cache"))
        self.metrics_path = _resolve_path(cache_cfg.get("metrics_file", "output/cache/cache_metrics.json"))
        self._metrics_lock = threading.RLock()

    def get(self, namespace: str, key: str, force_refresh: bool = False) -> Any | None:
        if not self.enabled or force_refresh:
            self._record(namespace, "bypass" if force_refresh else "disabled", {})
            logger.info("Cache %s for %s:%s", "bypass" if force_refresh else "disabled", namespace, key)
            return None

        entry = self.backend.get(namespace, key, self.ttls[namespace])
        if not entry:
            self._record(namespace, "miss", {})
            logger.info("Cache miss for %s:%s", namespace, key)
            return None

        self._record(namespace, "hit", entry.get("savings", {}))
        logger.info("Cache hit for %s:%s", namespace, key)
        return entry["value"]

    def set(
        self,
        namespace: str,
        key: str,
        value: Any,
        source: str,
        savings: dict[str, int] | None = None,
    ) -> Any:
        if not self.enabled:
            return value
        self.backend.set(namespace, key, value, source, savings)
        return value

    def _record(self, namespace: str, status: str, savings: dict[str, int]) -> None:
        with self._metrics_lock:
            metrics = _load_json(self.metrics_path)
            ns = metrics.setdefault(namespace, {
                "hits": 0,
                "misses": 0,
                "bypasses": 0,
                "disabled": 0,
                "estimated_api_calls_saved": 0,
                "estimated_tokens_saved": 0,
            })
            if status == "hit":
                ns["hits"] += 1
                ns["estimated_api_calls_saved"] += int(savings.get("api_calls", 0))
                ns["estimated_tokens_saved"] += int(savings.get("tokens", 0))
            elif status == "miss":
                ns["misses"] += 1
            elif status == "bypass":
                ns["bypasses"] += 1
            elif status == "disabled":
                ns["disabled"] += 1
            metrics["last_updated"] = _now().isoformat()
            _write_json(self.metrics_path, metrics)


def make_cache_key(*parts: Any) -> str:
    return json.dumps(parts, sort_keys=True, default=str)


def attach_cache_metadata(value: Any, status: str, source: str) -> Any:
    if isinstance(value, dict):
        enriched = dict(value)
        enriched["cache_metadata"] = {
            "status": status,
            "source": source,
            "last_updated": _now().isoformat(),
        }
        return enriched
    return value


def _resolve_path(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else _PROJECT_ROOT / p


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
