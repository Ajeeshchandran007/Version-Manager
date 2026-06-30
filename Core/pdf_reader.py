# Core/pdf_reader.py
"""
Reads the CURRENT installed version of software from a PDF document.

Uses a three-strategy cascade so it works regardless of document layout:
  1. TABLE  — structured tables (pdfplumber); most reliable when present
  2. LINE   — bullet / prose lines: "Software X, Version Y.Z and CU18"
  3. KV     — key-value blocks: software name on one line, version/CU below it

pdfminer is used for raw text extraction (strategies 2 & 3).
pdfplumber is used for table extraction (strategy 1).
No LLM calls are made — all extraction is regex-based.
"""
import os
import re
import pdfminer.high_level
import pdfplumber
from Utils.utils import logger, load_config


# ---------------------------------------------------------------------------
# Vendor prefix normalisation
#
# Document text sometimes drops or adds a vendor prefix relative to the
# canonical name used in software.yml / current_versions.json — e.g.
# canonical "MS Exchange Server 2019" appears in the PDF as just
# "Exchange Server 2019", or canonical "edge" appears as "Microsoft Edge".
#
# Instead of hardcoding a keyword-to-canonical entry for every individual
# piece of software (which silently breaks the moment someone adds a new
# entry to software.yml without also editing this file), we strip a small,
# generic set of vendor prefixes before comparing. New software added to
# software.yml is matched automatically — no changes needed here.
# ---------------------------------------------------------------------------
_VENDOR_PREFIXES: tuple[str, ...] = ("microsoft ", "ms ")


def _strip_vendor_prefix(name: str) -> str:
    """Strip a leading vendor token (e.g. 'MS', 'Microsoft') if present."""
    n = name.lower().strip()
    for prefix in _VENDOR_PREFIXES:
        if n.startswith(prefix):
            return n[len(prefix):]
    return n


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Collapse all whitespace runs to a single space.

    pdfminer often produces 'SQL  Server 2019' or 'Version  15.0.4043.16'
    with extra internal spaces.  Normalising before any comparison or regex
    prevents silent mismatches.
    """
    return re.sub(r'\s+', ' ', text or '').strip()


def _matches_software(text: str, software_name: str) -> bool:
    """Return True if *text* refers to *software_name* (case-insensitive).

    Checks a direct substring match first, then retries with any leading
    vendor prefix stripped from the canonical name (e.g. "MS Exchange
    Server 2019" -> "exchange server 2019") so callers can pass whatever
    canonical name lives in software.yml and still match PDF text that
    phrases the vendor differently or omits it entirely.
    """
    cell = _normalize(text).lower()
    name = software_name.lower()
    if name in cell:
        return True
    stripped = _strip_vendor_prefix(name)
    if stripped != name and stripped in cell:
        return True
    return False


def _extract_version_value(text: str) -> str | None:
    """Pull the first version-like token from *text*.

    Handles:
      15.0.4043.16    plain numeric
      1.1.1w          numeric + trailing letter  (OpenSSL)
      12.0.1 FP2      numeric + FP suffix        (HCL Domino)
      7.76.1          three-part numeric          (libCurl)
    """
    if not text:
        return None
    text = _normalize(text)
    m = re.search(r'([\d]+[\d.]*[a-zA-Z]?(?:\s*FP\d+)?)', text)
    return m.group(1).strip() if m else None


def _extract_cu_value(text: str) -> str | None:
    """Pull the CU label (e.g. 'CU18') from *text*, or None."""
    if not text:
        return None
    m = re.search(r'\bCU(\d+)\b', text, re.IGNORECASE)
    return f"CU{m.group(1)}" if m else None


def _is_version_header(text: str) -> bool:
    t = _normalize(text).lower()
    return any(k in t for k in ("version", "build", "release", "ver"))


def _is_cu_header(text: str) -> bool:
    t = _normalize(text).lower()
    return any(k in t for k in ("cu", "cumulative", "update", "patch"))


# ---------------------------------------------------------------------------
# Strategy 1 — TABLE extraction  (pdfplumber)
# ---------------------------------------------------------------------------

def _search_tables(pdf_path: str, software_name: str) -> dict | None:
    """Scan every table on every page for a row matching *software_name*.

    Works for layouts like:
      +-------------------------+-----------------+-----+
      | Software                | Build Version   | CU  |
      +-------------------------+-----------------+-----+
      | SQL Server 2019         | 15.0.4043.16    | CU18|
      +-------------------------+-----------------+-----+

    Falls back to cell-scanning when column headers are absent or ambiguous.
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                for table in (page.extract_tables() or []):
                    if not table or len(table) < 2:
                        continue

                    header = [_normalize(str(c or '')) for c in table[0]]
                    ver_col = next((i for i, h in enumerate(header) if _is_version_header(h)), None)
                    cu_col  = next((i for i, h in enumerate(header) if _is_cu_header(h)),  None)

                    for row in table[1:]:
                        if not row:
                            continue
                        if not any(_matches_software(str(c or ''), software_name) for c in row):
                            continue

                        build = (
                            _extract_version_value(str(row[ver_col]))
                            if ver_col is not None and ver_col < len(row) else None
                        )
                        cu = (
                            _extract_cu_value(str(row[cu_col]))
                            if cu_col is not None and cu_col < len(row) else None
                        )

                        # Header-less fallback: scan every cell
                        if not build:
                            for cell in row:
                                v = _extract_version_value(str(cell or ''))
                                if v:
                                    build = v
                                    break
                        if not cu:
                            for cell in row:
                                c = _extract_cu_value(str(cell or ''))
                                if c:
                                    cu = c
                                    break

                        if build or cu:
                            return {"Build Version": build, "Cumulative Update (CU)": cu,
                                    "_strategy": "table"}
    except Exception as exc:
        logger.warning(f"Table extraction failed: {exc}")
    return None


# ---------------------------------------------------------------------------
# Strategy 2 — LINE / PROSE extraction  (pdfminer text)
# ---------------------------------------------------------------------------

def _search_lines(pdf_text: str, software_name: str) -> dict | None:
    """Scan normalised text lines for software + version on the same/nearby line.

    Handles bullet-point and inline prose layouts such as:
      - Database Server: SQL Server 2019, Version 15.0.4043.16 and CU18
      - Security Library: OpenSSL, Version 1.1.1w

    Looks at a 3-line context window to catch wrapped text.
    """
    lines = pdf_text.splitlines()
    for i, line in enumerate(lines):
        if not _matches_software(_normalize(line), software_name):
            continue

        # Context: current + next 2 lines (version may wrap to next line)
        context = ' '.join(_normalize(l) for l in lines[i:i + 3])

        ver_match = re.search(
            r'Version\s+([\d]+[\d.]*[a-zA-Z]?(?:\s+FP\d+)?)',
            context, re.IGNORECASE,
        )
        cu_match = re.search(r'\bCU(\d+)\b', context, re.IGNORECASE)

        build = ver_match.group(1).strip() if ver_match else None
        cu    = f"CU{cu_match.group(1)}" if cu_match else None

        # Broader fallback: any version-like token in context
        if not build:
            m = re.search(r'\b(\d+\.\d+[\d.]*[a-zA-Z]?(?:\s+FP\d+)?)\b', context)
            if m:
                build = m.group(1).strip()

        if build or cu:
            return {"Build Version": build, "Cumulative Update (CU)": cu,
                    "_strategy": "line"}
    return None


# ---------------------------------------------------------------------------
# Strategy 3 — KEY-VALUE BLOCK extraction  (pdfminer text)
# ---------------------------------------------------------------------------

def _search_kv_blocks(pdf_text: str, software_name: str) -> dict | None:
    """Handle layouts where version info appears on lines *below* the name.

    Example:
      SQL Server 2019
      Build Version: 15.0.4043.16
      Cumulative Update: CU18

    Scans up to 5 lines below the software name line.
    """
    lines = [_normalize(l) for l in pdf_text.splitlines() if _normalize(l)]
    for i, line in enumerate(lines):
        if not _matches_software(line, software_name):
            continue
        build, cu = None, None
        for nearby in lines[i + 1: i + 6]:
            if re.search(r'\b(build|version|release)\b', nearby, re.IGNORECASE):
                v = _extract_version_value(nearby)
                if v:
                    build = v
            if re.search(r'\b(cu|cumulative|update|patch)\b', nearby, re.IGNORECASE):
                c = _extract_cu_value(nearby)
                if c:
                    cu = c
        if build or cu:
            return {"Build Version": build, "Cumulative Update (CU)": cu,
                    "_strategy": "kv_block"}
    return None


# ---------------------------------------------------------------------------
# Master extraction function
# ---------------------------------------------------------------------------

def _fetch_from_pdf(pdf_path: str, software_name: str) -> dict:
    """Try all three strategies in order; return the first hit."""

    # Strategy 1: tables (pdfplumber)
    result = _search_tables(pdf_path, software_name)
    if result:
        strategy = result.pop("_strategy")
        logger.info(f"PDFReader [{software_name}]: matched via {strategy}")
        result["source"] = f"PDF fallback \u2014 server unreachable (via {strategy})"
        return result

    # Strategies 2 & 3 share a single pdfminer text extraction pass
    try:
        with open(pdf_path, "rb") as f:
            pdf_text = pdfminer.high_level.extract_text(f)
    except Exception as exc:
        logger.error(f"PDFReader: pdfminer extraction failed: {exc}")
        return _empty()

    result = _search_lines(pdf_text, software_name)
    if result:
        strategy = result.pop("_strategy")
        logger.info(f"PDFReader [{software_name}]: matched via {strategy}")
        result["source"] = f"PDF fallback \u2014 server unreachable (via {strategy})"
        return result

    result = _search_kv_blocks(pdf_text, software_name)
    if result:
        strategy = result.pop("_strategy")
        logger.info(f"PDFReader [{software_name}]: matched via {strategy}")
        result["source"] = f"PDF fallback \u2014 server unreachable (via {strategy})"
        return result

    logger.warning(f"PDFReader: no version found for '{software_name}' in {pdf_path}")
    return _empty()


# ---------------------------------------------------------------------------
# PDFReader class  (public interface — unchanged from original)
# ---------------------------------------------------------------------------

class PDFReader:
    def __init__(self):
        config = load_config()
        raw_path = config["input_files"]["current_version_pdf"]
        # Anchor relative paths to the project root (same approach load_config()
        # uses for config.json) so this resolves correctly regardless of the
        # working directory the process was launched from. Absolute paths
        # passed via config are left untouched.
        if os.path.isabs(raw_path):
            self.pdf_path: str = raw_path
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.pdf_path = os.path.join(base_dir, raw_path)
        self._pdf_text: str | None = None  # not used externally; kept for compatibility

    async def fetch(self, software_name: str) -> dict:
        """Return {Build Version, Cumulative Update (CU), source} for one software."""
        logger.info(f"PDFReader: extracting current version for '{software_name}'")
        return _fetch_from_pdf(self.pdf_path, software_name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _empty() -> dict:
    return {
        "Build Version": None,
        "Cumulative Update (CU)": None,
        "source": "PDF fallback \u2014 server unreachable",
    }