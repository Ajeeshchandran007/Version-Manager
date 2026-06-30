"""Fetch compatibility requirements from vendor documentation/search results."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from langchain_tavily import TavilySearch

from Core.cache import CacheManager, make_cache_key
from Core.openai_client import OpenAIClient
from Utils.utils import config_mtime, logger, load_config


SYSTEM_PROMPT = """Extract software compatibility requirements from vendor documentation or search results.

Return ONLY valid JSON with these exact keys:
{
  "Supported OS": "...",
  "Supported Runtime": "...",
  "Supported Browser": "...",
  "Database Dependency": "...",
  "Supported Architecture": "...",
  "Requirement Source": "...",
  "Requirement Source URL": "...",
  "Requirement Confidence": "High|Medium|Low|Not available"
}

Rules:
- Use only information present in the provided vendor/search content.
- If the content confirms there is no dependency, use "Not applicable".
- If the content is unclear or missing, use "Not available".
- Do not invent version support.
- Requirement Source should be "Vendor Documentation" when content is from vendor docs, otherwise "Search Result".
"""


REQUIREMENT_FIELDS = [
    "Supported OS",
    "Supported Runtime",
    "Supported Browser",
    "Database Dependency",
    "Supported Architecture",
    "Requirement Source",
    "Requirement Source URL",
    "Requirement Confidence",
]


class CompatibilityRequirementFetcher:
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
            logger.info("CompatibilityRequirementFetcher: config changed; refreshing settings.")
            self._configure()

    async def fetch(
        self,
        software_name: str,
        latest_version: str | None = None,
        source_url: str | None = None,
        force_refresh: bool = False,
    ) -> dict[str, str] | None:
        self._refresh_if_config_changed()
        latest_version = str(latest_version or "").strip()
        source_url = str(source_url or "").strip()
        cache_key = make_cache_key("compatibility_requirements", software_name, latest_version, source_url)
        cached = self.cache.get("openai_analysis", cache_key, force_refresh=force_refresh)
        if cached:
            return cached

        query = (
            f"{software_name} {latest_version} system requirements supported operating system "
            "runtime browser database architecture vendor documentation"
        ).strip()
        try:
            tavily_key = make_cache_key("compatibility_tavily", query, source_url)
            results = self.cache.get("tavily", tavily_key, force_refresh=force_refresh)
            if not results:
                results = self.tavily.invoke({"query": query})
                self.cache.set("tavily", tavily_key, results, source="tavily", savings={"api_calls": 1, "tokens": 0})
        except Exception as exc:
            logger.warning("Compatibility vendor search failed for %s: %s", software_name, exc)
            return None

        if isinstance(results, dict):
            results = results.get("results", [])
        if not results:
            return None

        search_text = "\n\n---\n\n".join(
            f"URL: {item.get('url', '')}\nTitle: {item.get('title', '')}\nContent: {item.get('content', '')}"
            for item in results
        )
        user_prompt = (
            f"Software: {software_name}\n"
            f"Latest Version: {latest_version or 'Not available'}\n"
            f"Preferred Vendor Source URL: {source_url or 'Not available'}\n\n"
            f"Search/Vendor Content:\n{search_text}"
        )
        raw = await self.llm.extract(SYSTEM_PROMPT, user_prompt)
        parsed = _parse_requirements(raw, source_url)
        if not parsed:
            return None
        parsed["Last Verified"] = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        return self.cache.set(
            "openai_analysis",
            cache_key,
            parsed,
            source="vendor_compatibility_extraction",
            savings={"api_calls": 2, "tokens": max(1, len(SYSTEM_PROMPT + user_prompt + str(raw)) // 4)},
        )


def _parse_requirements(raw: str | None, fallback_url: str) -> dict[str, str] | None:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    normalized = {field: _clean_value(data.get(field)) for field in REQUIREMENT_FIELDS}
    if not normalized["Requirement Source URL"] and fallback_url:
        normalized["Requirement Source URL"] = fallback_url
    return normalized


def _clean_value(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text or "Not available"
