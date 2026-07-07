"""Agent registry for the Version Manager multi-agent workflow."""
from __future__ import annotations

from agent.contracts import AgentDefinition


DISCOVERY_TOOLS = ("get_software_list", "query_server", "extract_from_pdf", "get_active_config")
RESEARCH_TOOLS = ("search_latest_version",)
ANALYSIS_TOOLS = ("compare_versions", "get_run_history")
SECURITY_TOOLS = ("check_vulnerabilities", "save_vulnerability_report")
PACKAGE_READINESS_TOOLS = (
    "run_package_flow",
    "assess_package_readiness",
    "save_package_readiness",
    "get_package_dashboard",
    "get_package_readiness_summary",
    "get_blocked_packages",
    "get_package_checklist",
)
COMPATIBILITY_TOOLS = ("check_compatibility",)
QA_VALIDATION_TOOLS = (
    "run_qa_flow",
    "generate_qa_validation",
    "save_qa_validation",
    "generate_testcase_impact",
    "get_qa_dashboard",
    "get_testcase_coverage",
    "get_failed_qa_items",
    "get_qa_testers",
)
REPORTING_TOOLS = (
    "run_shared_scan",
    "generate_excel_assessment",
    "send_notification",
    "log_audit_event",
    "get_output_files",
    "get_release_artifacts",
)


AGENT_REGISTRY: dict[str, AgentDefinition] = {
    "planner": AgentDefinition(
        name="planner",
        description="Determines the next workflow step from release state.",
        allowed_tools=(),
        produced_outputs=("next_agent", "workflow_plan"),
    ),
    "discovery": AgentDefinition(
        name="discovery",
        description="Collect configured software and current installed versions.",
        allowed_tools=DISCOVERY_TOOLS,
        produced_outputs=("software_inventory",),
    ),
    "research": AgentDefinition(
        name="research",
        description="Collect latest version metadata.",
        allowed_tools=RESEARCH_TOOLS,
        required_inputs=("software_inventory",),
        produced_outputs=("latest_versions",),
    ),
    "analysis": AgentDefinition(
        name="analysis",
        description="Compare current versions to latest versions and attach run history.",
        allowed_tools=ANALYSIS_TOOLS,
        required_inputs=("software_inventory", "latest_versions"),
        produced_outputs=("comparison_results",),
    ),
    "security": AgentDefinition(
        name="security",
        description="Assess vulnerabilities and security posture.",
        allowed_tools=SECURITY_TOOLS,
        required_inputs=("comparison_results",),
        produced_outputs=("vulnerability_results",),
    ),
    "package_readiness": AgentDefinition(
        name="package_readiness",
        description="Assess package readiness for release engineering.",
        allowed_tools=PACKAGE_READINESS_TOOLS,
        required_inputs=("comparison_results", "vulnerability_results"),
        produced_outputs=("package_readiness_results",),
        role_visibility=("admin", "release_engineer"),
    ),
    "compatibility": AgentDefinition(
        name="compatibility",
        description="Assess compatibility requirements.",
        allowed_tools=COMPATIBILITY_TOOLS,
        required_inputs=("comparison_results", "package_readiness_results"),
        produced_outputs=("compatibility_results",),
    ),
    "qa_validation": AgentDefinition(
        name="qa_validation",
        description="Generate QA validation and test case impact outputs.",
        allowed_tools=QA_VALIDATION_TOOLS,
        required_inputs=("comparison_results", "package_readiness_results"),
        produced_outputs=("qa_validation_results", "testcase_impact_results"),
        role_visibility=("admin", "qa_engineer"),
    ),
    "reporting": AgentDefinition(
        name="reporting",
        description="Generate final report package, notification, and audit event.",
        allowed_tools=REPORTING_TOOLS,
        required_inputs=("comparison_results", "vulnerability_results"),
        produced_outputs=("report", "report_package"),
    ),
    "verifier": AgentDefinition(
        name="verifier",
        description="Verifies specialist outputs and bounds retry loops.",
        allowed_tools=(),
        required_inputs=("last_agent",),
        produced_outputs=("verification_result",),
    ),
}


def get_agent_definition(name: str) -> AgentDefinition:
    return AGENT_REGISTRY[name]
