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
from agent.memory import init_db, log_audit, save_run_result


AgentName = Literal[
    "discovery",
    "research",
    "analysis",
    "security",
    "package_readiness",
    "compatibility",
    "qa_validation",
    "reporting",
    "end",
]


class VersionManagerState(TypedDict, total=False):
    user_request: str
    category: str
    run_id: str
    force_refresh: bool

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

    messages: list[dict[str, Any]]
    audit_records: list[dict[str, Any]]


ToolMap = dict[str, Callable[..., Any]]


DISCOVERY_TOOLS = ["get_software_list", "query_server", "extract_from_pdf"]
RESEARCH_TOOLS = ["search_latest_version"]
ANALYSIS_TOOLS = ["compare_versions", "get_run_history"]
SECURITY_TOOLS = ["check_vulnerabilities", "save_vulnerability_report"]
PACKAGE_READINESS_TOOLS = ["assess_package_readiness", "save_package_readiness"]
COMPATIBILITY_TOOLS = ["check_compatibility"]
QA_VALIDATION_TOOLS = ["generate_qa_validation", "save_qa_validation", "generate_testcase_impact"]
REPORTING_TOOLS = ["generate_excel_assessment", "send_notification", "log_audit_event"]


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


class SupervisorAgent:
    """Entry point and router for the Version Manager workflow."""

    name = "supervisor"

    async def __call__(self, state: VersionManagerState) -> VersionManagerState:
        next_agent = self._choose_next_agent(state)
        status = "completed" if next_agent == "end" else "running"
        return {
            "next_agent": next_agent,
            "workflow_status": status,
            "messages": state.get("messages", [])
            + [{"role": "agent", "name": self.name, "content": f"Routing to {next_agent}."}],
        }

    def _choose_next_agent(self, state: VersionManagerState) -> AgentName:
        if not state.get("software_inventory"):
            return "discovery"
        if not state.get("latest_versions"):
            return "research"
        if not state.get("comparison_results"):
            return "analysis"
        if not state.get("vulnerability_results"):
            return "security"
        if not state.get("package_readiness_results"):
            return "package_readiness"
        if not state.get("compatibility_results"):
            return "compatibility"
        if not state.get("qa_validation_results"):
            return "qa_validation"
        if not state.get("report"):
            return "reporting"
        return "end"


class DiscoveryAgent(BaseSpecializedAgent):
    name = "discovery"
    allowed_tools = DISCOVERY_TOOLS

    async def __call__(self, state: VersionManagerState) -> VersionManagerState:
        category = state.get("category", "ALL")
        software = await self.tools["get_software_list"](category=category)
        names = software.get("software", [])
        inventory: list[dict[str, Any]] = []

        for name in names:
            current = await self.tools["query_server"](software_name=name)
            source = "live server"
            if not _has_version(current):
                current = await self.tools["extract_from_pdf"](software_name=name)
                source = current.get("source", "PDF fallback")
            inventory.append({"software_name": name, "current": current, "source": source})

        return {
            "software_inventory": inventory,
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
            latest[name] = await self.tools["search_latest_version"](
                software_name=name,
                force_refresh=bool(state.get("force_refresh", False)),
            )

        return {
            "latest_versions": latest,
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
        comparison = await self.tools["compare_versions"](
            latest=state.get("latest_versions", {}),
            current=current,
        )

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
            result["history"] = await self.tools["get_run_history"](
                software_name=software,
                limit=5,
            )

        return {
            "comparison_results": comparison,
            "messages": state.get("messages", [])
            + [self._message(f"Analyzed compliance for {len(comparison)} item(s).")],
        }


class SecurityAgent(BaseSpecializedAgent):
    name = "security"
    allowed_tools = SECURITY_TOOLS

    async def __call__(self, state: VersionManagerState) -> VersionManagerState:
        findings = {}
        for software, result in state.get("comparison_results", {}).items():
            findings[software] = await self.tools["check_vulnerabilities"](
                software_name=software,
                version=(result.get("current") or {}).get("Build Version"),
                needs_update=bool(result.get("needs_update")),
                force_refresh=bool(state.get("force_refresh", False)),
            )
        await self.tools["save_vulnerability_report"](vulnerabilities=findings)

        return {
            "vulnerability_results": findings,
            "messages": state.get("messages", [])
            + [self._message(f"Completed security assessment for {len(findings)} item(s).")],
        }


class PackageReadinessAgent(BaseSpecializedAgent):
    name = "package_readiness"
    allowed_tools = PACKAGE_READINESS_TOOLS

    async def __call__(self, state: VersionManagerState) -> VersionManagerState:
        result = await self.tools["assess_package_readiness"](
            comparison=state.get("comparison_results", {}),
            latest=state.get("latest_versions", {}),
            vulnerabilities=state.get("vulnerability_results", {}),
        )
        readiness = result.get("package_readiness", {})
        await self.tools["save_package_readiness"](package_readiness=readiness)

        return {
            "package_readiness_results": readiness,
            "messages": state.get("messages", [])
            + [self._message(f"Assessed package readiness for {len(readiness)} item(s).")],
        }


class CompatibilityAgent(BaseSpecializedAgent):
    name = "compatibility"
    allowed_tools = COMPATIBILITY_TOOLS

    async def __call__(self, state: VersionManagerState) -> VersionManagerState:
        result = await self.tools["check_compatibility"](
            comparison=state.get("comparison_results", {}),
            package_readiness=state.get("package_readiness_results", {}),
        )
        compatibility = result.get("compatibility", {})

        return {
            "compatibility_results": compatibility,
            "messages": state.get("messages", [])
            + [self._message(f"Checked compatibility requirements for {len(compatibility)} item(s).")],
        }


class QAValidationAgent(BaseSpecializedAgent):
    name = "qa_validation"
    allowed_tools = QA_VALIDATION_TOOLS

    async def __call__(self, state: VersionManagerState) -> VersionManagerState:
        result = await self.tools["generate_qa_validation"](
            comparison=state.get("comparison_results", {}),
            package_readiness=state.get("package_readiness_results", {}),
        )
        qa_validation = result.get("qa_validation", {})
        await self.tools["save_qa_validation"](qa_validation=qa_validation)
        testcase_result = await self.tools["generate_testcase_impact"](
            comparison=state.get("comparison_results", {}),
        )
        testcase_impact = testcase_result.get("testcase_impact", {})

        return {
            "qa_validation_results": qa_validation,
            "testcase_impact_results": testcase_impact,
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

        excel = await self.tools["generate_excel_assessment"]()
        notification = await self.tools["send_notification"](report={"body": report})
        audit = await self.tools["log_audit_event"](
            step="multi_agent_workflow_completed",
            details={
                "run_id": state.get("run_id"),
                "category": state.get("category"),
                "total": len(comparison),
                "notification": notification,
            },
        )

        return {
            "report": report,
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
            "messages": state.get("messages", [])
            + [self._message("Generated report package, notification, and audit record.")],
        }


class LangGraphVersionManager:
    """Compiled LangGraph StateGraph facade for callers."""

    def __init__(self, tools: ToolMap, run_id: str | None = None):
        init_db()
        self.run_id = run_id or str(uuid.uuid4())[:8]
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
            "workflow_status": "started",
            "messages": [{"role": "user", "content": user_request}],
        }
        final_state = await self.graph.ainvoke(initial)
        log_audit(self.run_id, "workflow_end", "supervisor", {"status": final_state.get("workflow_status")})
        return final_state

    def _build_graph(self, tools: ToolMap) -> StateGraph:
        workflow = StateGraph(VersionManagerState)
        workflow.add_node("supervisor", SupervisorAgent())
        workflow.add_node("discovery", DiscoveryAgent(tools))
        workflow.add_node("research", ResearchAgent(tools))
        workflow.add_node("analysis", AnalysisAgent(tools))
        workflow.add_node("security", SecurityAgent(tools))
        workflow.add_node("package_readiness", PackageReadinessAgent(tools))
        workflow.add_node("compatibility", CompatibilityAgent(tools))
        workflow.add_node("qa_validation", QAValidationAgent(tools))
        workflow.add_node("reporting", ReportingAgent(tools))

        workflow.add_edge(START, "supervisor")
        workflow.add_conditional_edges(
            "supervisor",
            lambda state: state["next_agent"],
            {
                "discovery": "discovery",
                "research": "research",
                "analysis": "analysis",
                "security": "security",
                "package_readiness": "package_readiness",
                "compatibility": "compatibility",
                "qa_validation": "qa_validation",
                "reporting": "reporting",
                "end": END,
            },
        )
        for node in [
            "discovery",
            "research",
            "analysis",
            "security",
            "package_readiness",
            "compatibility",
            "qa_validation",
            "reporting",
        ]:
            workflow.add_edge(node, "supervisor")
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
