# Core/comparator.py
"""Compares latest vs current versions and flags mismatches."""
from Utils.utils import logger


def compare(
    latest: dict[str, dict],
    current: dict[str, dict],
) -> dict[str, dict]:
    """
    Args:
        latest:  { software_name: {Build Version, Cumulative Update (CU)} }
        current: { software_name: {Build Version, Cumulative Update (CU)} }
    Returns:
        { software_name: { latest, current, build_match, cu_match, needs_update } }
    """
    report: dict[str, dict] = {}

    all_software = set(latest) | set(current)
    for name in all_software:
        l = latest.get(name, {})
        c = current.get(name, {})

        l_build = l.get("Build Version")
        c_build = c.get("Build Version")
        l_cu    = l.get("Cumulative Update (CU)")
        c_cu    = c.get("Cumulative Update (CU)")

        build_unknown = _both_unknown(l_build, c_build)
        cu_unknown    = _both_unknown(l_cu, c_cu)
        unknown       = build_unknown and cu_unknown
        build_match   = False if build_unknown else _known_versions_equal(l_build, c_build)
        cu_match      = False if unknown else _optional_versions_equal(l_cu, c_cu)
        needs_update  = not unknown and (not build_match or not cu_match)

        report[name] = {
            "latest":       {"Build Version": l_build, "Cumulative Update (CU)": l_cu},
            "current":      {"Build Version": c_build, "Cumulative Update (CU)": c_cu},
            "current_source": c.get("source", "unknown"),
            "build_match":  build_match,
            "cu_match":     cu_match,
            "unknown":      unknown,
            "needs_update": needs_update,
        }
        logger.info(f"Comparison [{name}]: needs_update={needs_update}, unknown={unknown}")

    return report


def _known_versions_equal(a: str | None, b: str | None) -> bool:
    """Case-insensitive comparison for known values."""
    if a is None or b is None:
        return False
    return a.strip().lower() == b.strip().lower()


def _optional_versions_equal(a: str | None, b: str | None) -> bool:
    """Case-insensitive comparison where both missing values mean not applicable."""
    if a is None and b is None:
        return True
    return _known_versions_equal(a, b)


def _both_unknown(a: str | None, b: str | None) -> bool:
    """True when neither side produced a usable value."""
    if a is None and b is None:
        return True
    return False
