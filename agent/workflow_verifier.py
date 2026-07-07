"""Verifier for LangGraph workflow outputs with bounded retries."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.workflow_planner import PRODUCED_STATE_BY_AGENT


MAX_VERIFICATION_RETRIES_PER_AGENT = 1


@dataclass(frozen=True)
class WorkflowVerification:
    passed: bool
    next_agent: str
    missing_outputs: tuple[str, ...]
    reason: str
    retry_counts: dict[str, int]


class WorkflowVerifier:
    """Checks specialist output completeness and prevents infinite retry loops."""

    def verify(self, state: dict[str, Any]) -> WorkflowVerification:
        last_agent = str(state.get("last_agent") or "")
        retry_counts = dict(state.get("verification_retries") or {})
        if not last_agent or last_agent == "reporting":
            return WorkflowVerification(True, "planner", (), "No specialist output requires verification.", retry_counts)

        expected_outputs = PRODUCED_STATE_BY_AGENT.get(last_agent, ())
        missing = tuple(field for field in expected_outputs if not _has_value(state.get(field)))
        if not missing:
            return WorkflowVerification(True, "planner", (), f"{last_agent} output verified.", retry_counts)

        retry_counts[last_agent] = retry_counts.get(last_agent, 0) + 1
        if retry_counts[last_agent] <= MAX_VERIFICATION_RETRIES_PER_AGENT:
            return WorkflowVerification(
                False,
                last_agent,
                missing,
                f"{last_agent} missed {', '.join(missing)}. Retrying once.",
                retry_counts,
            )

        return WorkflowVerification(
            False,
            "end",
            missing,
            f"{last_agent} missed {', '.join(missing)} after retry limit.",
            retry_counts,
        )


def _has_value(value: Any) -> bool:
    return value is not None and value != {} and value != [] and value != ""
