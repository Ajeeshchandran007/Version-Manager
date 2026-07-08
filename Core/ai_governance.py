"""AI governance policy for assistant routing, web search, and final answers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


EVIDENCE_FIRST_ORDER = (
    "generated_output",
    "mcp_app_tool",
    "approved_web_search",
    "ai_fallback",
)

WEB_SEARCH_ALLOWED_CATEGORIES = {
    "software_version",
    "vendor_release_notes",
    "cve_security_advisory",
    "compatibility_support_matrix",
    "product_documentation",
}

WEB_SEARCH_BLOCKED_CATEGORIES = {
    "general_internet",
    "people_news_politics",
    "unrelated_business_consumer",
    "internal_release_data",
}


@dataclass(frozen=True)
class GovernanceDecision:
    allowed: bool
    category: str
    reason: str = ""


@dataclass(frozen=True)
class ResponseVerification:
    passed: bool
    warnings: tuple[str, ...] = ()
    blocked: bool = False
    blocked_reason: str = ""


def classify_web_search_prompt(prompt: str, *, team: str = "", release: str = "", known_software: list[str] | None = None) -> GovernanceDecision:
    prompt_lower = prompt.lower()
    known = [name.lower() for name in (known_software or []) if name]

    if _contains_any(prompt_lower, ("president", "prime minister", "election", "war news", "stock price", "celebrity", "sports score")):
        return GovernanceDecision(False, "people_news_politics", "Web search is blocked for people, news, politics, sports, or market prompts.")

    if _is_internal_package_prompt(prompt_lower, team=team, release=release):
        return GovernanceDecision(False, "internal_release_data", "Package readiness is internal release data and must come from generated project outputs.")
    if _is_internal_artifact_prompt(prompt_lower, team=team, release=release):
        return GovernanceDecision(False, "internal_release_data", "Release artifacts are internal project outputs and must come from generated project files.")

    if any(name and name in prompt_lower for name in known):
        return GovernanceDecision(True, "software_version")

    if _contains_any(prompt_lower, ("release notes", "vendor advisory", "download", "patch notes")):
        return GovernanceDecision(True, "vendor_release_notes")
    if _contains_any(prompt_lower, ("cve", "vulnerability", "security advisory", "security bulletin")):
        return GovernanceDecision(True, "cve_security_advisory")
    if _contains_any(prompt_lower, ("compatibility", "compatible", "support matrix", "system requirement", "browser support", "processor requirement")):
        return GovernanceDecision(True, "compatibility_support_matrix")
    if _contains_any(prompt_lower, ("version", "build", "cu", "cumulative update", "software", "product documentation")):
        return GovernanceDecision(True, "product_documentation")

    if team and team.lower() in prompt_lower and _contains_any(prompt_lower, ("release", "version", "software", "compatibility", "vulnerability")):
        return GovernanceDecision(True, "product_documentation")
    if release and release.lower() in prompt_lower and _contains_any(prompt_lower, ("release", "version", "software", "compatibility", "vulnerability")):
        return GovernanceDecision(True, "product_documentation")

    return GovernanceDecision(
        False,
        "general_internet",
        "Web search is limited to software versions, vendor release notes, CVE/security advisories, compatibility/support matrices, and product documentation.",
    )


def verify_final_assistant_response(
    *,
    prompt: str,
    content: str,
    source: str,
    role: str,
    team: str = "",
    release: str = "",
) -> ResponseVerification:
    warnings: list[str] = []
    source_text = str(source or "").strip()
    content_lower = str(content or "").lower()
    prompt_lower = str(prompt or "").lower()

    if not source_text:
        warnings.append("missing source label")

    if role == "QA Engineer" and _is_internal_package_prompt(prompt_lower, team=team, release=release):
        if not source_text.startswith("Access guardrail"):
            return ResponseVerification(
                passed=False,
                blocked=True,
                blocked_reason="Package readiness is owned by Release Assistant and is not available in QA Assistant.",
                warnings=("qa package access not blocked",),
            )

    if source_text == "Used AI fallback" and _looks_like_specific_project_fact(prompt_lower):
        warnings.append("AI fallback used for a prompt that appears to require project evidence")

    if source_text == "Used web search" and _is_internal_package_prompt(prompt_lower, team=team, release=release):
        return ResponseVerification(
            passed=False,
            blocked=True,
            blocked_reason="Web search cannot answer internal package readiness. Use generated project outputs or Release Assistant.",
            warnings=("web search attempted for internal release data",),
        )
    if source_text == "Used web search" and _is_internal_artifact_prompt(prompt_lower, team=team, release=release):
        return ResponseVerification(
            passed=False,
            blocked=True,
            blocked_reason="Web search cannot answer internal release artifacts. Use generated project outputs from the selected release.",
            warnings=("web search attempted for internal release artifacts",),
        )

    if source_text.startswith(("Used MCP tool", "Used web search")) and not content_lower.strip():
        warnings.append("empty sourced response")

    return ResponseVerification(passed=not warnings, warnings=tuple(warnings))


def apply_final_governance(
    *,
    prompt: str,
    content: str,
    source: str,
    role: str,
    team: str = "",
    release: str = "",
) -> tuple[str, str, ResponseVerification]:
    verification = verify_final_assistant_response(
        prompt=prompt,
        content=content,
        source=source,
        role=role,
        team=team,
        release=release,
    )
    if verification.blocked:
        return verification.blocked_reason, "Access guardrail: AI governance", verification
    if verification.passed:
        return content, source, verification
    note = "; ".join(verification.warnings)
    return f"{content}\n\nGovernance note: {note}.", source or "Used AI fallback", verification


def _contains_any(value: str, terms: tuple[str, ...]) -> bool:
    return any(term in value for term in terms)


def _is_internal_package_prompt(prompt_lower: str, *, team: str = "", release: str = "") -> bool:
    has_package = _contains_any(prompt_lower, ("package", "packaging"))
    if not has_package:
        return False
    has_internal_action = _contains_any(prompt_lower, ("ready", "readiness", "blocked", "blocker", "status"))
    has_context = any(value and value.lower() in prompt_lower for value in (team, release, "sourceone", "dps", "release"))
    return has_internal_action and has_context


def _is_internal_artifact_prompt(prompt_lower: str, *, team: str = "", release: str = "") -> bool:
    has_artifact = _contains_any(prompt_lower, ("artifact", "artifacts", "output files", "generated files", "reports"))
    if not has_artifact:
        return False
    has_context = any(value and value.lower() in prompt_lower for value in (team, release, "sourceone", "dps", "release"))
    return has_context


def _looks_like_specific_project_fact(prompt_lower: str) -> bool:
    return _contains_any(
        prompt_lower,
        (
            "current version",
            "deployed version",
            "latest version",
            "testcase",
            "test coverage",
            "qa signoff",
            "signed off",
            "package readiness",
            "ready for packaging",
            "vulnerability",
            "report available",
            "release artifacts",
            "artifacts",
        ),
    )
