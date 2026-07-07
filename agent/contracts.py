"""Typed contracts shared by Version Manager agents and assistant tools."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ToolSource = Literal["app_tool", "mcp_tool", "ai_fallback", "file", "unknown"]


@dataclass(frozen=True)
class ToolResult:
    """Standard shape for app/MCP tool results before assistant formatting."""

    success: bool
    source: str
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    paths: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    source_type: ToolSource = "app_tool"
    widget: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "source": self.source,
            "message": self.message,
            "data": self.data,
            "paths": self.paths,
            "errors": self.errors,
            "source_type": self.source_type,
            "widget": self.widget,
        }


def tool_result_envelope(
    *,
    success: bool = True,
    source: str,
    message: str = "",
    data: dict[str, Any] | None = None,
    paths: dict[str, str] | None = None,
    errors: list[str] | None = None,
    source_type: ToolSource = "app_tool",
    context: dict[str, Any] | None = None,
    **legacy_fields: Any,
) -> dict[str, Any]:
    """Return a normalized tool result while preserving legacy top-level fields."""

    payload = dict(legacy_fields)
    result_data = dict(data or {})
    if context:
        result_data.setdefault("context", context)
    payload.update(
        {
            "success": success,
            "source": source,
            "message": message,
            "data": result_data,
            "paths": dict(paths or {}),
            "errors": list(errors or []),
            "source_type": source_type,
        }
    )
    return payload


def unwrap_tool_data(result: Any) -> dict[str, Any]:
    """Read normalized or legacy tool results as a plain data dictionary."""

    if not isinstance(result, dict):
        return {}
    data = result.get("data")
    return data if isinstance(data, dict) else result


@dataclass(frozen=True)
class AgentDefinition:
    """Metadata for one workflow agent."""

    name: str
    description: str
    allowed_tools: tuple[str, ...]
    required_inputs: tuple[str, ...] = ()
    produced_outputs: tuple[str, ...] = ()
    role_visibility: tuple[str, ...] = ("admin", "release_engineer", "qa_engineer")


@dataclass(frozen=True)
class AssistantPlan:
    """Planner decision for a user question."""

    intent: str
    preferred_source: ToolSource
    tool_name: str
    needs_ai_fallback: bool = False
    reason: str = ""


@dataclass(frozen=True)
class VerificationResult:
    """Quality gate for assistant answers before they are shown."""

    passed: bool
    warnings: tuple[str, ...] = ()
