"""Assistant response verifier for source/context quality checks."""
from __future__ import annotations

from agent.context import ReleaseContext
from agent.contracts import ToolResult, VerificationResult


def verify_assistant_response(result: ToolResult, context: ReleaseContext) -> VerificationResult:
    warnings: list[str] = []
    content = result.message.lower()

    if result.source_type in {"app_tool", "mcp_tool", "file"} and not result.source:
        warnings.append("missing tool source")
    if result.success and context.team and context.release and context.team.lower() not in content:
        if "context" not in result.data:
            warnings.append("missing release context")
    if result.success and result.source.endswith("Test Case Impact") and not result.paths.get("testcase_impact"):
        warnings.append("missing testcase impact source path")

    return VerificationResult(passed=not warnings, warnings=tuple(warnings))


def append_verification_note(message: str, verification: VerificationResult) -> str:
    if verification.passed:
        return message
    note = "; ".join(verification.warnings)
    return f"{message}\n\nNote: I found a response quality warning: {note}."
