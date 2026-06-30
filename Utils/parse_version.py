# Utils/parse_version.py
"""Single shared parser used by both VersionFetcher and PDFReader."""
import re
from Utils.utils import logger

_BUILD_RE = re.compile(r"^\s*-\s*Build Version:\s*(.+)", re.IGNORECASE)
_CU_RE    = re.compile(r"^\s*-\s*Cumulative Update \(CU\):\s*(.+)", re.IGNORECASE)


def parse_version_text(text: str) -> dict:
    """
    Parses LLM output into:
        { "Build Version": str|None, "Cumulative Update (CU)": str|None }
    """
    result = {"Build Version": None, "Cumulative Update (CU)": None}

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        m = _BUILD_RE.match(line)
        if m:
            val = m.group(1).strip()
            if "not found" not in val.lower():
                # Strip leading 'v' prefix (e.g. v9.4.2 → 9.4.2)
                val = re.sub(r"^v", "", val, flags=re.IGNORECASE)
                # Must contain digits to be a real version
                if re.search(r"\d", val):
                    result["Build Version"] = val
            continue

        m = _CU_RE.match(line)
        if m:
            val = m.group(1).strip()
            if "not found" not in val.lower():
                # Only accept CUxx format — reject bare build numbers like 15.0.4430.1
                cu = re.search(r"\bCU\d+\b", val, re.IGNORECASE)
                result["Cumulative Update (CU)"] = cu.group(0).upper() if cu else None
            continue

    logger.debug(f"Parsed: {result}")
    return result