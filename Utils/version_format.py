"""Version formatting helpers shared by comparison, reports, and UI."""
from __future__ import annotations

import re
from typing import Any


_EXCHANGE_PARTIAL_RE = re.compile(r"^\s*(\d{3,4})\.(\d{1,3})\s*$")
_FOUR_PART_VERSION_RE = re.compile(r"^\s*(\d+)\.(\d+)\.(\d+)\.(\d+)\s*$")


def canonical_version(software_name: str, version: Any) -> str:
    """Return a display/comparison canonical version for known products."""
    if version is None:
        return ""
    text = str(version).strip()
    if not text:
        return ""
    if "exchange" in software_name.lower():
        return _canonical_exchange_version(text)
    return text


def _canonical_exchange_version(version: str) -> str:
    """Normalize Exchange versions to long ExSetup format.

    Examples:
      15.2.858.10 -> 15.02.0858.010
      15.02.1748.046 -> 15.02.1748.046
      1748.037 -> 15.02.1748.037
    """
    match = _FOUR_PART_VERSION_RE.match(version)
    if match:
        major, minor, build, revision = (int(part) for part in match.groups())
        return f"{major:02d}.{minor:02d}.{build:04d}.{revision:03d}"

    partial = _EXCHANGE_PARTIAL_RE.match(version)
    if partial:
        build, revision = (int(part) for part in partial.groups())
        return f"15.02.{build:04d}.{revision:03d}"

    return version
