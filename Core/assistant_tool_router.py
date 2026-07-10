"""Hybrid semantic assistant tool router with optional OpenAI embeddings."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI

from Utils.utils import load_config, logger


CATALOG_VERSION = "2026-07-07.1"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_THRESHOLD = 0.72
DEFAULT_MARGIN = 0.06


@dataclass(frozen=True)
class AssistantToolDefinition:
    name: str
    source_label: str
    description: str
    examples: tuple[str, ...]
    aliases: tuple[str, ...]
    allowed_roles: tuple[str, ...]
    blocked_roles: tuple[str, ...] = ()
    data_classification: str = "Internal"
    internal: bool = True


@dataclass(frozen=True)
class ToolRouteDecision:
    selected_tool: str = ""
    confidence: float = 0.0
    method: str = "none"
    allowed: bool = False
    denied_reason: str = ""
    source_label: str = ""
    data_classification: str = ""
    second_tool: str = ""
    second_confidence: float = 0.0


TOOL_CATALOG: tuple[AssistantToolDefinition, ...] = (
    AssistantToolDefinition(
        name="release_reports",
        source_label="Used MCP tool: Release Reports",
        description="Lists generated release reports, evidence files, output files, deliverables, and artifacts.",
        examples=(
            "show release artifacts",
            "what did workflow produce",
            "show generated outputs",
            "show release evidence package",
            "what reports are available",
            "which files were generated",
            "show workflow deliverables",
        ),
        aliases=("artifact", "artifacts", "reports", "output files", "generated files", "evidence", "deliverables"),
        allowed_roles=("Admin", "Release Engineer"),
        blocked_roles=("QA Engineer",),
        data_classification="Release Restricted",
    ),
    AssistantToolDefinition(
        name="package_readiness",
        source_label="Used MCP tool: Package Readiness",
        description="Summarizes package readiness, package blockers, and packaging status.",
        examples=(
            "what is ready for packaging",
            "package readiness summary",
            "which packages are blocked",
            "show package blockers",
            "is the package ready",
            "what checklist is pending",
            "show pending package checklist",
        ),
        aliases=(
            "package readiness",
            "packaging",
            "package blocker",
            "ready for packaging",
            "blocked package",
            "package checklist",
            "pending checklist",
            "checklist pending",
        ),
        allowed_roles=("Admin", "Release Engineer"),
        blocked_roles=("QA Engineer",),
        data_classification="Release Restricted",
    ),
    AssistantToolDefinition(
        name="qa_validation",
        source_label="Used MCP tool: QA Validation",
        description="Shows QA dashboard, QA status, signoff readiness, and validation posture.",
        examples=("show QA dashboard", "QA status", "is this ready for signoff", "show QA summary", "QA validation status"),
        aliases=("qa dashboard", "qa status", "signoff", "sign-off", "validation"),
        allowed_roles=("Admin", "Release Engineer", "QA Engineer"),
    ),
    AssistantToolDefinition(
        name="testcase_impact",
        source_label="Used MCP tool: Test Case Impact",
        description="Answers test coverage, testcase impact, recommended test cases, and missing coverage questions.",
        examples=(
            "which software has no testcase coverage",
            "how many recommended test cases",
            "show test impact",
            "missing test coverage",
            "what tests are recommended",
        ),
        aliases=("testcase", "test case", "test coverage", "coverage", "recommended tests"),
        allowed_roles=("Admin", "Release Engineer", "QA Engineer"),
    ),
    AssistantToolDefinition(
        name="current_version",
        source_label="Used MCP tool: Current Version Output",
        description="Finds current, installed, deployed, or existing software versions from generated outputs.",
        examples=("what is current version of OpenSSL", "installed version of libcurl", "deployed version of SQL Server"),
        aliases=("current version", "installed version", "deployed version", "existing version"),
        allowed_roles=("Admin", "Release Engineer", "QA Engineer"),
    ),
    AssistantToolDefinition(
        name="latest_version",
        source_label="Used MCP tool: Latest Version Output",
        description="Finds latest software versions from generated output or release summaries.",
        examples=("latest version of OpenSSL", "latest software version used in release", "newest build for libcurl"),
        aliases=("latest version", "latest build", "newest version", "latest software"),
        allowed_roles=("Admin", "Release Engineer", "QA Engineer"),
    ),
    AssistantToolDefinition(
        name="vulnerability_assessment",
        source_label="Used MCP tool: Vulnerability Assessment",
        description="Summarizes CVE, vulnerability, security risk, and security assessment outputs.",
        examples=("summarize CVE risk", "show vulnerability assessment", "OpenSSL security risk", "security posture"),
        aliases=("vulnerability", "vulnerabilities", "cve", "security risk", "security assessment"),
        allowed_roles=("Admin", "Release Engineer", "QA Engineer"),
        data_classification="Security Restricted",
    ),
    AssistantToolDefinition(
        name="release_context",
        source_label="Used MCP tool: Release Context",
        description="Answers current, active, or selected release context questions.",
        examples=("what is current release", "which release is selected", "show active release context"),
        aliases=("current release", "active release", "selected release", "release context"),
        allowed_roles=("Admin", "Release Engineer", "QA Engineer"),
    ),
)


def resolve_assistant_tool(
    prompt: str,
    *,
    role: str,
    team: str = "",
    release: str = "",
    config: dict[str, Any] | None = None,
) -> ToolRouteDecision:
    config = config if config is not None else load_config()
    router_config = config.get("assistant_router", {}) if isinstance(config.get("assistant_router"), dict) else {}
    threshold = float(router_config.get("embedding_threshold", DEFAULT_THRESHOLD))
    margin = float(router_config.get("embedding_margin", DEFAULT_MARGIN))

    deterministic = _deterministic_scores(prompt, team=team, release=release)
    embedding = _embedding_scores(prompt, config=config) if _embedding_enabled(config) else {}
    combined = _combine_scores(deterministic, embedding)
    if not combined:
        return ToolRouteDecision()

    ranked = sorted(combined.items(), key=lambda item: item[1][0], reverse=True)
    top_tool, (top_score, method) = ranked[0]
    second_tool, (second_score, _) = ranked[1] if len(ranked) > 1 else ("", (0.0, "none"))
    if top_score < threshold and not (top_score >= 0.55 and deterministic.get(top_tool, 0.0) >= 0.45):
        return ToolRouteDecision(second_tool=second_tool, second_confidence=second_score)
    if second_score and top_score - second_score < margin and deterministic.get(top_tool, 0.0) < 0.75:
        return ToolRouteDecision(second_tool=second_tool, second_confidence=second_score)

    definition = _tool_by_name(top_tool)
    if not definition:
        return ToolRouteDecision()
    if role in definition.blocked_roles or (definition.allowed_roles and role not in definition.allowed_roles):
        return ToolRouteDecision(
            selected_tool=definition.name,
            confidence=top_score,
            method=method,
            allowed=False,
            denied_reason=f"{definition.name.replace('_', ' ').title()} is not available for {role}.",
            source_label="Access guardrail: QA role" if role == "QA Engineer" else f"Access guardrail: {role}",
            data_classification=definition.data_classification,
            second_tool=second_tool,
            second_confidence=second_score,
        )
    return ToolRouteDecision(
        selected_tool=definition.name,
        confidence=top_score,
        method=method,
        allowed=True,
        source_label=definition.source_label,
        data_classification=definition.data_classification,
        second_tool=second_tool,
        second_confidence=second_score,
    )


def _deterministic_scores(prompt: str, *, team: str = "", release: str = "") -> dict[str, float]:
    normalized = _normalize(prompt)
    prompt_tokens = set(normalized.split())
    scores: dict[str, float] = {}
    for tool in TOOL_CATALOG:
        score = 0.0
        for alias in tool.aliases:
            alias_normalized = _normalize(alias)
            if alias_normalized and alias_normalized in normalized:
                score = max(score, 0.86)
            alias_tokens = set(alias_normalized.split())
            if alias_tokens:
                overlap = len(prompt_tokens & alias_tokens) / len(alias_tokens)
                score = max(score, overlap * 0.62)
        for example in tool.examples:
            example_tokens = set(_normalize(example).split())
            if example_tokens:
                overlap = len(prompt_tokens & example_tokens) / len(example_tokens)
                score = max(score, overlap * 0.78)
        if team and team.lower() in prompt.lower():
            score += 0.03
        if release and release.lower() in prompt.lower():
            score += 0.03
        if score:
            scores[tool.name] = min(score, 0.99)
    return scores


def _embedding_enabled(config: dict[str, Any]) -> bool:
    router_config = config.get("assistant_router", {}) if isinstance(config.get("assistant_router"), dict) else {}
    enabled = str(router_config.get("embedding_enabled", "false")).lower() in {"1", "true", "yes", "on"}
    api_key = str(config.get("openai_api_key") or "").strip()
    return enabled and bool(api_key) and not api_key.startswith("${")


def _embedding_scores(prompt: str, *, config: dict[str, Any]) -> dict[str, float]:
    try:
        embeddings = _load_or_build_tool_embeddings(config)
        prompt_vector = _embed_texts([prompt], config)[0]
    except Exception as exc:
        logger.warning("Assistant embedding router unavailable: %s", exc)
        return {}
    scores: dict[str, float] = {}
    for row in embeddings:
        tool = str(row.get("tool") or "")
        vector = row.get("embedding") if isinstance(row.get("embedding"), list) else []
        if tool and vector:
            scores[tool] = max(scores.get(tool, 0.0), _cosine(prompt_vector, vector))
    return scores


def _load_or_build_tool_embeddings(config: dict[str, Any]) -> list[dict[str, Any]]:
    router_config = config.get("assistant_router", {}) if isinstance(config.get("assistant_router"), dict) else {}
    model = str(router_config.get("embedding_model") or DEFAULT_EMBEDDING_MODEL)
    cache_path = Path(str(router_config.get("embedding_cache") or "output/cache/assistant_tool_embeddings.json"))
    catalog_hash = _catalog_hash()
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            if cache.get("catalog_version") == CATALOG_VERSION and cache.get("catalog_hash") == catalog_hash and cache.get("model") == model:
                rows = cache.get("embeddings") if isinstance(cache.get("embeddings"), list) else []
                if rows:
                    return rows
        except json.JSONDecodeError:
            pass
    rows = []
    texts = []
    owners = []
    for tool in TOOL_CATALOG:
        for example in (*tool.examples, *tool.aliases, tool.description):
            texts.append(example)
            owners.append(tool.name)
    vectors = _embed_texts(texts, config)
    for tool_name, text, vector in zip(owners, texts, vectors):
        rows.append({"tool": tool_name, "text": text, "embedding": vector})
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({"catalog_version": CATALOG_VERSION, "catalog_hash": catalog_hash, "model": model, "embeddings": rows}),
        encoding="utf-8",
    )
    return rows


def _embed_texts(texts: list[str], config: dict[str, Any]) -> list[list[float]]:
    router_config = config.get("assistant_router", {}) if isinstance(config.get("assistant_router"), dict) else {}
    model = str(router_config.get("embedding_model") or DEFAULT_EMBEDDING_MODEL)
    client = OpenAI(api_key=str(config.get("openai_api_key") or ""))
    response = client.embeddings.create(model=model, input=texts)
    return [list(item.embedding) for item in response.data]


def _combine_scores(deterministic: dict[str, float], embedding: dict[str, float]) -> dict[str, tuple[float, str]]:
    combined: dict[str, tuple[float, str]] = {}
    for tool_name in set(deterministic) | set(embedding):
        det = deterministic.get(tool_name, 0.0)
        emb = embedding.get(tool_name, 0.0)
        combined[tool_name] = (max(det, emb), "embedding" if emb > det else "deterministic")
    return combined


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", "".join(ch.lower() if ch.isalnum() else " " for ch in str(value or ""))).strip()


def _tool_by_name(name: str) -> AssistantToolDefinition | None:
    for tool in TOOL_CATALOG:
        if tool.name == name:
            return tool
    return None


def _catalog_hash() -> str:
    payload = [
        {"name": tool.name, "examples": tool.examples, "aliases": tool.aliases, "roles": tool.allowed_roles, "blocked": tool.blocked_roles}
        for tool in TOOL_CATALOG
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
