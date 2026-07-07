"""Deterministic workflow planner for the LangGraph release pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


WORKFLOW_SEQUENCE = (
    "discovery",
    "research",
    "analysis",
    "security",
    "package_readiness",
    "compatibility",
    "qa_validation",
    "reporting",
)


REQUIRED_STATE_BY_AGENT = {
    "discovery": (),
    "research": ("software_inventory",),
    "analysis": ("software_inventory", "latest_versions"),
    "security": ("comparison_results",),
    "package_readiness": ("comparison_results", "vulnerability_results"),
    "compatibility": ("comparison_results", "package_readiness_results"),
    "qa_validation": ("comparison_results", "package_readiness_results"),
    "reporting": ("comparison_results", "vulnerability_results", "qa_validation_results"),
}


PRODUCED_STATE_BY_AGENT = {
    "discovery": ("software_inventory",),
    "research": ("latest_versions",),
    "analysis": ("comparison_results",),
    "security": ("vulnerability_results",),
    "package_readiness": ("package_readiness_results",),
    "compatibility": ("compatibility_results",),
    "qa_validation": ("qa_validation_results", "testcase_impact_results"),
    "reporting": ("report", "report_package"),
}


@dataclass(frozen=True)
class WorkflowPlan:
    next_agent: str
    reason: str
    pending_outputs: tuple[str, ...]


class WorkflowPlanner:
    """Chooses the next deterministic workflow step from current state."""

    def plan(self, state: dict[str, Any]) -> WorkflowPlan:
        if state.get("workflow_status") == "failed":
            return WorkflowPlan("end", "Workflow already failed.", ())
        for agent_name in WORKFLOW_SEQUENCE:
            produced = PRODUCED_STATE_BY_AGENT[agent_name]
            missing_outputs = tuple(field for field in produced if not _has_value(state.get(field)))
            if missing_outputs:
                missing_inputs = tuple(
                    field for field in REQUIRED_STATE_BY_AGENT[agent_name]
                    if not _has_value(state.get(field))
                )
                if missing_inputs:
                    upstream = self._agent_for_output(missing_inputs[0])
                    return WorkflowPlan(
                        upstream,
                        f"{agent_name} is waiting for {', '.join(missing_inputs)}.",
                        missing_inputs,
                    )
                return WorkflowPlan(
                    agent_name,
                    f"{agent_name} must produce {', '.join(missing_outputs)}.",
                    missing_outputs,
                )
        return WorkflowPlan("end", "All required workflow outputs are present.", ())

    def _agent_for_output(self, output_field: str) -> str:
        for agent_name, outputs in PRODUCED_STATE_BY_AGENT.items():
            if output_field in outputs:
                return agent_name
        return "end"


def _has_value(value: Any) -> bool:
    return value is not None and value != {} and value != [] and value != ""
