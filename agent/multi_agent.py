"""LangGraph multi-agent workflow for Version Manager.

Each specialized agent receives a narrow MCP tool map. Agents communicate by
returning partial updates to the shared VersionManagerState only; external work
is delegated to MCP tools.
"""
from __future__ import annotations

import uuid
from typing import Any, Callable, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from Core.notifier import build_report
from Utils.utils import logger
from agent.context import ReleaseContext
from agent.contracts import unwrap_tool_data
from agent.memory import init_db, log_audit, save_run_result
from agent.registry import (
    AGENT_REGISTRY,
    ANALYSIS_TOOLS,
    COMPATIBILITY_TOOLS,
    DISCOVERY_TOOLS,
    PACKAGE_READINESS_TOOLS,
    QA_VALIDATION_TOOLS,
    REPORTING_TOOLS,
    RESEARCH_TOOLS,
    SECURITY_TOOLS,
)
from agent.workflow_planner import WORKFLOW_SEQUENCE, WorkflowPlanner
from agent.workflow_verifier import WorkflowVerifier


AgentName = Literal[
    "planner",
    "discovery",
    "research",
    "analysis",
    "security",
    "package_readiness",
    "compatibility",
    "qa_validation",
    "reporting",
    "verifier",
    "end",
]


class VersionManagerState(TypedDict, total=False):
    user_request: str
    category: str
    run_id: str
    force_refresh: bool
    release_context: dict[str, str]

    software_inventory: list[dict[str, Any]]
    latest_versions: dict[str, dict[str, Any]]

    comparison_results: dict[str, dict[str, Any]]
    vulnerability_results: dict[str, dict[str, Any]]
    package_readiness_results: dict[str, dict[str, Any]]
    compatibility_results: dict[str, dict[str, Any]]
    qa_validation_results: dict[str, dict[str, Any]]
    testcase_impact_results: dict[str, Any]

    report: str
    report_package: dict[str, Any]

    next_agent: AgentName
    workflow_status: str
    workflow_plan: dict[str, Any]
    verification_result: dict[str, Any]
    verification_retries: dict[str, int]
    last_agent: str

    messages: list[dict[str, Any]]
    audit_records: list[dict[str, Any]]


ToolMap = dict[str, Callable[..., Any]]


class BaseSpecializedAgent:
    """Base class that enforces each agent's MCP tool allow-list."""

    name = "base"
    allowed_tools: list[str] = []

    def __init__(self, tools: ToolMap):
        missing = [tool for tool in self.allowed_tools if tool not in tools]
        if missing:
            raise ValueError(f"{self.name} missing MCP tools: {', '.join(missing)}")
        self.tools = {tool: tools[tool] for tool in self.allowed_tools}

    def _message(self, content: str) -> dict[str, str]:
        return {"role": "agent", "name": self.name, "content": content}


class PlannerAgent:
    """Deterministic planner for the Version Manager workflow."""

    name = "planner"

    async def __call__(self, state: VersionManagerState) -> VersionManagerState:
        plan = WorkflowPlanner().plan(state)
        next_agent = plan.next_agent
        status = "completed" if next_agent == "end" else "running"
        return {
            "next_agent": next_agent,
            "workflow_status": status,
            "workflow_plan": {
                "next_agent": plan.next_agent,
                "reason": plan.reason,
                "pending_outputs": list(plan.pending_outputs),
            },
            "messages": state.get("messages", [])
            + [{"role": "agent", "name": self.name, "content": f"Planning route to {next_agent}: {plan.reason}"}],
        }


class VerifierAgent:
    """Quality gate for specialist outputs with bounded retry behavior."""

    name = "verifier"

    async def __call__(self, state: VersionManagerState) -> VersionManagerState:
        verification = WorkflowVerifier().verify(state)
        failed = verification.next_agent == "end" and not verification.passed
        return {
            "next_agent": verification.next_agent,
            "workflow_status": "failed" if failed else "running",
            "verification_retries": verification.retry_counts,
            "verification_result": {
                "passed": verification.passed,
                "next_agent": verification.next_agent,
                "missing_outputs": list(verification.missing_outputs),
                "reason": verification.reason,
            },
            "messages": state.get("messages", [])
            + [{"role": "agent", "name": self.name, "content": verification.reason}],
        }


class DiscoveryAgent(BaseSpecializedAgent):
    name = "discovery"
    allowed_tools = DISCOVERY_TOOLS

    async def __call__(self, state: VersionManagerState) -> VersionManagerState:
        category = state.get("category", "ALL")
        software = unwrap_tool_data(await self.tools["get_software_list"](category=category))
        names = software.get("software", [])
        inventory: list[dict[str, Any]] = []

        for name in names:
            current = unwrap_tool_data(await self.tools["query_server"](software_name=name))
            source = "live server"
            if not _has_version(current):
                current = unwrap_tool_data(await self.tools["extract_from_pdf"](software_name=name))
                source = current.get("source", "PDF fallback")
            inventory.append({"software_name": name, "current": current, "source": source})

        return {
            "software_inventory": inventory,
            "last_agent": self.name,
            "messages": state.get("messages", [])
            + [self._message(f"Discovered {len(inventory)} software item(s).")],
        }


class ResearchAgent(BaseSpecializedAgent):
    name = "research"
    allowed_tools = RESEARCH_TOOLS

    async def __call__(self, state: VersionManagerState) -> VersionManagerState:
        latest = {}
        for item in state.get("software_inventory", []):
            name = item["software_name"]
            latest[name] = unwrap_tool_data(await self.tools["search_latest_version"](
                software_name=name,
                force_refresh=bool(state.get("force_refresh", False)),
            ))

        return {
            "latest_versions": latest,
            "last_agent": self.name,
            "messages": state.get("messages", [])
            + [self._message(f"Collected latest version metadata for {len(latest)} item(s).")],
        }


class AnalysisAgent(BaseSpecializedAgent):
    name = "analysis"
    allowed_tools = ANALYSIS_TOOLS

    async def __call__(self, state: VersionManagerState) -> VersionManagerState:
        current = {
            item["software_name"]: {
                **(item.get("current") or {}),
                "source": item.get("source", "unknown"),
            }
            for item in state.get("software_inventory", [])
        }
        comparison = unwrap_tool_data(await self.tools["compare_versions"](
            latest=state.get("latest_versions", {}),
            current=current,
        ))

        category = state.get("category", "ALL")
        run_id = state.get("run_id", "")
        for software, result in comparison.items():
            current_version = result.get("current", {}) or {}
            save_run_result(
                run_id=run_id,
                software=software,
                category=category,
                build_ver=current_version.get("Build Version"),
                cu_ver=current_version.get("Cumulative Update (CU)"),
                source=result.get("current_source", "unknown"),
                needs_update=bool(result.get("needs_update")),
            )
            result["history"] = unwrap_tool_data(await self.tools["get_run_history"](
                software_name=software,
                limit=5,
            ))

        return {
            "comparison_results": comparison,
            "last_agent": self.name,
            "messages": state.get("messages", [])
            + [self._message(f"Analyzed compliance for {len(comparison)} item(s).")],
        }


class SecurityAgent(BaseSpecializedAgent):
    name = "security"
    allowed_tools = SECURITY_TOOLS

    async def __call__(self, state: VersionManagerState) -> VersionManagerState:
        findings = {}
        for software, result in state.get("comparison_results", {}).items():
            findings[software] = unwrap_tool_data(await self.tools["check_vulnerabilities"](
                software_name=software,
                version=(result.get("current") or {}).get("Build Version"),
                needs_update=bool(result.get("needs_update")),
                force_refresh=bool(state.get("force_refresh", False)),
            ))
        await self.tools["save_vulnerability_report"](vulnerabilities=findings)

        return {
            "vulnerability_results": findings,
            "last_agent": self.name,
            "messages": state.get("messages", [])
            + [self._message(f"Completed security assessment for {len(findings)} item(s).")],
        }


class PackageReadinessAgent(BaseSpecializedAgent):
    name = "package_readiness"
    allowed_tools = PACKAGE_READINESS_TOOLS

    async def __call__(self, state: VersionManagerState) -> VersionManagerState:
        result = unwrap_tool_data(await self.tools["assess_package_readiness"](
            comparison=state.get("comparison_results", {}),
            latest=state.get("latest_versions", {}),
            vulnerabilities=state.get("vulnerability_results", {}),
        ))
        readiness = result.get("package_readiness", {})
        await self.tools["save_package_readiness"](package_readiness=readiness)

        return {
            "package_readiness_results": readiness,
            "last_agent": self.name,
            "messages": state.get("messages", [])
            + [self._message(f"Assessed package readiness for {len(readiness)} item(s).")],
        }


class CompatibilityAgent(BaseSpecializedAgent):
    name = "compatibility"
    allowed_tools = COMPATIBILITY_TOOLS

    async def __call__(self, state: VersionManagerState) -> VersionManagerState:
        result = unwrap_tool_data(await self.tools["check_compatibility"](
            comparison=state.get("comparison_results", {}),
            package_readiness=state.get("package_readiness_results", {}),
        ))
        compatibility = result.get("compatibility", {})

        return {
            "compatibility_results": compatibility,
            "last_agent": self.name,
            "messages": state.get("messages", [])
            + [self._message(f"Checked compatibility requirements for {len(compatibility)} item(s).")],
        }


class QAValidationAgent(BaseSpecializedAgent):
    name = "qa_validation"
    allowed_tools = QA_VALIDATION_TOOLS

    async def __call__(self, state: VersionManagerState) -> VersionManagerState:
        result = unwrap_tool_data(await self.tools["generate_qa_validation"](
            comparison=state.get("comparison_results", {}),
            package_readiness=state.get("package_readiness_results", {}),
        ))
        qa_validation = result.get("qa_validation", {})
        await self.tools["save_qa_validation"](qa_validation=qa_validation)
        testcase_result = unwrap_tool_data(await self.tools["generate_testcase_impact"](
            comparison=state.get("comparison_results", {}),
        ))
        testcase_impact = testcase_result.get("testcase_impact", {})

        return {
            "qa_validation_results": qa_validation,
            "testcase_impact_results": testcase_impact,
            "last_agent": self.name,
            "messages": state.get("messages", [])
            + [self._message(
                f"Generated QA validation plan for {len(qa_validation)} item(s) "
                f"and mapped {testcase_impact.get('summary', {}).get('total_recommended_test_cases', 0)} recommended test case(s)."
            )],
        }


class ReportingAgent(BaseSpecializedAgent):
    name = "reporting"
    allowed_tools = REPORTING_TOOLS

    async def __call__(self, state: VersionManagerState) -> VersionManagerState:
        comparison = state.get("comparison_results", {})
        vulnerabilities = state.get("vulnerability_results", {})
        report = build_report(comparison, vulnerabilities)

        excel = unwrap_tool_data(await self.tools["generate_excel_assessment"]())
        notification = unwrap_tool_data(await self.tools["send_notification"](report={"body": report}))
        audit = unwrap_tool_data(await self.tools["log_audit_event"](
            step="multi_agent_workflow_completed",
            details={
                "run_id": state.get("run_id"),
                "category": state.get("category"),
                "release_context": state.get("release_context", {}),
                "total": len(comparison),
                "notification": notification,
            },
        ))

        return {
            "report": report,
            "workflow_status": "completed",
            "report_package": {
                "comparison": comparison,
                "vulnerabilities": vulnerabilities,
                "package_readiness": state.get("package_readiness_results", {}),
                "compatibility": state.get("compatibility_results", {}),
                "qa_validation": state.get("qa_validation_results", {}),
                "testcase_impact": state.get("testcase_impact_results", {}),
                "excel": excel,
                "notification": notification,
                "audit": audit,
            },
            "last_agent": self.name,
            "messages": state.get("messages", [])
            + [self._message("Generated report package, notification, and audit record.")],
        }


class LangGraphVersionManager:
    """Compiled LangGraph StateGraph facade for callers."""

    def __init__(self, tools: ToolMap, run_id: str | None = None, release_context: ReleaseContext | None = None):
        init_db()
        self.run_id = run_id or str(uuid.uuid4())[:8]
        self.release_context = release_context
        self.graph = self._build_graph(tools).compile()

    async def run(
        self,
        user_request: str,
        category: str = "ALL",
        force_refresh: bool = False,
    ) -> VersionManagerState:
        logger.info("[%s] Multi-agent workflow started. category=%s", self.run_id, category)
        log_audit(self.run_id, "workflow_start", "supervisor", {"request": user_request, "category": category})
        initial: VersionManagerState = {
            "user_request": user_request,
            "category": category,
            "run_id": self.run_id,
            "force_refresh": force_refresh,
            "release_context": self.release_context.as_dict() if self.release_context else {},
            "workflow_status": "started",
            "verification_retries": {},
            "messages": [{"role": "user", "content": user_request}],
        }
        final_state = await self.graph.ainvoke(initial)
        log_audit(self.run_id, "workflow_end", "supervisor", {"status": final_state.get("workflow_status")})
        return final_state

    def _build_graph(self, tools: ToolMap) -> StateGraph:
        workflow = StateGraph(VersionManagerState)
        workflow.add_node("planner", PlannerAgent())
        workflow.add_node("verifier", VerifierAgent())
        agent_classes = {
            "discovery": DiscoveryAgent,
            "research": ResearchAgent,
            "analysis": AnalysisAgent,
            "security": SecurityAgent,
            "package_readiness": PackageReadinessAgent,
            "compatibility": CompatibilityAgent,
            "qa_validation": QAValidationAgent,
            "reporting": ReportingAgent,
        }
        for name in WORKFLOW_SEQUENCE:
            workflow.add_node(name, agent_classes[name](tools))

        workflow.add_edge(START, "planner")
        workflow.add_conditional_edges(
            "planner",
            lambda state: state["next_agent"],
            {**{name: name for name in WORKFLOW_SEQUENCE}, "end": END},
        )
        for node in WORKFLOW_SEQUENCE:
            if node == "reporting":
                workflow.add_edge(node, END)
            else:
                workflow.add_edge(node, "verifier")
        workflow.add_conditional_edges(
            "verifier",
            lambda state: state["next_agent"],
            {**{name: name for name in WORKFLOW_SEQUENCE}, "planner": "planner", "end": END},
        )
        return workflow


def _has_version(result: dict[str, Any]) -> bool:
    return bool(result.get("Build Version") or result.get("Cumulative Update (CU)"))


def _security_summary(findings: dict[str, dict[str, Any]]) -> str:
    lines = []
    for software, result in findings.items():
        critical = result.get("critical_cves", [])
        severity = result.get("risk_level", "UNKNOWN")
        lines.append(f"{software}: {severity}; critical CVEs: {', '.join(critical) or 'none'}")
    return "\n".join(lines)
