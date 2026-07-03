# Core/version_fetcher.py
"""Fetches the LATEST version of software from the web using Tavily + OpenAI."""
import datetime
import re
from html import unescape
import httpx
from langchain_tavily import TavilySearch
from Core.cache import CacheManager, attach_cache_metadata, make_cache_key
from Core.openai_client import OpenAIClient
from Utils.parse_version import parse_version_text
from Utils.utils import config_mtime, logger, load_config
from Utils.version_format import canonical_version

SYSTEM_PROMPT = """You are an assistant that extracts software version info from search results.
Find the absolute latest Build Version and Cumulative Update (CU).

Rules:
- Build Version: the numeric version string only (e.g. 15.0.4430.1, 8.1.2, 3.0.2). Strip any leading 'v'.
- Cumulative Update (CU): ONLY the CU label (e.g. CU32, CU15). Never put a build number here.
  If there is no CU, output 'Not Found'.

Output ONLY these two lines:
- Build Version: [value or 'Not Found']
- Cumulative Update (CU): [value or 'Not Found']"""


class VersionFetcher:
    def __init__(self):
        self._config_mtime = 0.0
        self._configure()

    def _configure(self) -> None:
        config = load_config()
        self.cache = CacheManager(config)
        self.tavily = TavilySearch(max_results=5, tavily_api_key=config["tavily_api_key"])
        self.llm = OpenAIClient()
        self._config_mtime = config_mtime()

    def _refresh_if_config_changed(self) -> None:
        current_mtime = config_mtime()
        if current_mtime and current_mtime != self._config_mtime:
            logger.info("VersionFetcher: config changed; refreshing cache/search settings.")
            self._configure()

    async def fetch(self, software_name: str, force_refresh: bool = False) -> dict:
        """Return {Build Version: ..., Cumulative Update (CU): ...} for one software."""
        self._refresh_if_config_changed()
        logger.info(f"VersionFetcher: fetching latest for '{software_name}'")
        final_key = make_cache_key("latest_version", software_name)
        cached = self.cache.get("software_versions", final_key, force_refresh=force_refresh)
        if cached:
            return attach_cache_metadata(cached, "hit", "software_versions")

        authoritative = await _fetch_authoritative_latest(software_name, self.cache, force_refresh)
        if authoritative:
            return self.cache.set(
                "software_versions",
                final_key,
                attach_cache_metadata(authoritative, "miss", "vendor_sources"),
                source="vendor_sources",
                savings={"api_calls": 3, "tokens": 1500},
            )

        # 1. Search
        try:
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            query = f"Latest build version and cumulative update for {software_name} as of {today}"
            tavily_key = make_cache_key("tavily", query)
            results = self.cache.get("tavily", tavily_key, force_refresh=force_refresh)
            if not results:
                results = self.tavily.invoke({"query": query})
                self.cache.set(
                    "tavily",
                    tavily_key,
                    results,
                    source="tavily",
                    savings={"api_calls": 1, "tokens": 0},
                )
        except Exception as e:
            logger.error(f"Tavily error for {software_name}: {e}")
            return _empty()

        # Tavily returns a dict like {"results": [...], ...} — extract the list
        if isinstance(results, dict):
            results = results.get("results", [])

        if not results:
            logger.warning(f"No search results for {software_name}")
            return _empty()

        search_text = "\n\n---\n\n".join(
            f"Source: {r.get('url','N/A')}\n{r.get('content','')}" for r in results
        )

        # 2. LLM extraction
        user_prompt = f"Software: {software_name}\n\nSearch Results:\n{search_text}"
        openai_key = make_cache_key("openai_extract", SYSTEM_PROMPT, user_prompt)
        raw = self.cache.get("openai_analysis", openai_key, force_refresh=force_refresh)
        if not raw:
            raw = await self.llm.extract(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )
            if raw:
                self.cache.set(
                    "openai_analysis",
                    openai_key,
                    raw,
                    source="openai",
                    savings={"api_calls": 1, "tokens": _estimate_tokens(SYSTEM_PROMPT + user_prompt + raw)},
                )
        if not raw:
            return _empty()

        # 3. Parse and repair deterministic table relationships if needed.
        parsed = parse_version_text(raw)
        parsed["Build Version"] = canonical_version(software_name, parsed.get("Build Version"))
        _fill_cu_from_search_text(parsed, search_text)
        return self.cache.set(
            "software_versions",
            final_key,
            attach_cache_metadata(parsed, "miss", "software_versions"),
            source="software_versions",
            savings={"api_calls": 2, "tokens": _estimate_tokens(SYSTEM_PROMPT + user_prompt + raw)},
        )


def _empty() -> dict:
    return {"Build Version": None, "Cumulative Update (CU)": None}


def _fill_cu_from_search_text(parsed: dict, search_text: str) -> None:
    """
    Some vendor tables list SQL Server GDR rows as "CU32 + GDR 15.0.x".
    The LLM may extract the build and miss the CU label. Repair that by
    mapping the extracted build version back to the CU label from source text.
    """
    if parsed.get("Cumulative Update (CU)") or not parsed.get("Build Version"):
        return

    build = re.escape(parsed["Build Version"])
    patterns = [
        rf"\b(CU\d+)\s*(?:\([^)]*\))?\s*\+\s*GDR\s+{build}\b",
        rf"\b(CU\d+)\s*(?:\([^)]*\))?\s+{build}\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, search_text, flags=re.IGNORECASE)
        if match:
            parsed["Cumulative Update (CU)"] = match.group(1).upper()
            logger.info(
                "Repaired CU from source table: build=%s cu=%s",
                parsed["Build Version"],
                parsed["Cumulative Update (CU)"],
            )
            return


async def _fetch_authoritative_latest(
    software_name: str,
    cache: CacheManager | None = None,
    force_refresh: bool = False,
) -> dict | None:
    """Fetch known vendor pages directly where table structure is reliable."""
    normalized_name = software_name.strip().lower()
    if normalized_name == "sql server 2019":
        return await _fetch_sql_server_latest(cache, force_refresh)
    if "exchange" in normalized_name and "2019" in normalized_name:
        return await _fetch_exchange_2019_latest(cache, force_refresh)
    return None


async def _fetch_sql_server_latest(
    cache: CacheManager | None = None,
    force_refresh: bool = False,
) -> dict | None:
    url = "https://learn.microsoft.com/en-us/troubleshoot/sql/releases/sqlserver-2019/build-versions"
    cache_key = make_cache_key("vendor_source", url)
    html = cache.get("vendor_sources", cache_key, force_refresh=force_refresh) if cache else None
    try:
        if not html:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                html = response.text
            if cache:
                cache.set(
                    "vendor_sources",
                    cache_key,
                    html,
                    source="microsoft-learn",
                    savings={"api_calls": 1, "tokens": 0},
                )
    except Exception as exc:
        logger.warning("Authoritative SQL Server lookup failed: %s", exc)
        return None

    rows = []
    for match in re.finditer(r"\b(CU\d+)\s*(?:\([^)]*\))?\s*\+\s*GDR\s+(\d+(?:\.\d+){2,})\b", html, re.IGNORECASE):
        rows.append((match.group(2), match.group(1).upper()))

    if not rows:
        for match in re.finditer(r"\b(CU\d+)\s*(?:\([^)]*\))?\s+(\d+(?:\.\d+){2,})\b", html, re.IGNORECASE):
            rows.append((match.group(2), match.group(1).upper()))

    if not rows:
        return None

    build, cu = max(rows, key=lambda item: _version_tuple(item[0]))
    logger.info("Authoritative SQL Server lookup: build=%s cu=%s", build, cu)
    return {"Build Version": build, "Cumulative Update (CU)": cu}


async def _fetch_exchange_2019_latest(
    cache: CacheManager | None = None,
    force_refresh: bool = False,
) -> dict | None:
    """Fetch the latest Exchange 2019 row from Microsoft Learn.

    Exchange search snippets can expose partial build numbers such as
    "1748.037". The authoritative table provides both the short and long
    formats; use the long format so comparison with ExSetup ProductVersion is
    apples-to-apples.
    """
    url = "https://learn.microsoft.com/en-us/exchange/new-features/build-numbers-and-release-dates"
    cache_key = make_cache_key("vendor_source", url)
    html = cache.get("vendor_sources", cache_key, force_refresh=force_refresh) if cache else None
    try:
        if not html:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                html = response.text
            if cache:
                cache.set(
                    "vendor_sources",
                    cache_key,
                    html,
                    source="microsoft-learn",
                    savings={"api_calls": 1, "tokens": 0},
                )
    except Exception as exc:
        logger.warning("Authoritative Exchange 2019 lookup failed: %s", exc)
        return None

    text = _html_to_text(html)
    section_match = re.search(
        r"Exchange Server 2019(?P<section>.*?)(?:Exchange Server 2016|Exchange Server 2013|$)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    section = section_match.group("section") if section_match else text
    rows: list[tuple[str, str]] = []
    for match in re.finditer(
        r"Exchange Server 2019\s+(CU\d+)[^\n]*?\b\d{1,2}\.\d{1,2}\.\d+\.\d+\b\s+"
        r"(?P<long>\d{2}\.\d{2}\.\d{4}\.\d{3})",
        section,
        flags=re.IGNORECASE,
    ):
        rows.append((match.group("long"), match.group(1).upper()))

    if not rows:
        for match in re.finditer(
            r"Exchange Server 2019\s+(CU\d+)[^\n]*?(?P<short>\d{1,2}\.\d{1,2}\.\d+\.\d+)",
            section,
            flags=re.IGNORECASE,
        ):
            rows.append((_normalize_exchange_version(match.group("short")), match.group(1).upper()))

    if not rows:
        return None

    build, cu = max(rows, key=lambda item: _version_tuple(item[0]))
    logger.info("Authoritative Exchange 2019 lookup: build=%s cu=%s", build, cu)
    return {"Build Version": build, "Cumulative Update (CU)": cu}


def _html_to_text(html: str) -> str:
    text = re.sub(r"<script\b.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"[ \t\r\f\v]+", " ", text)


def _normalize_exchange_version(version: str) -> str:
    parts = version.split(".")
    if len(parts) != 4:
        return version
    return f"{int(parts[0]):02d}.{int(parts[1]):02d}.{int(parts[2]):04d}.{int(parts[3]):03d}"


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split(".") if part.isdigit())


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)
