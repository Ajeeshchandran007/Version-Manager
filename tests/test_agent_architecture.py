import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.context import build_release_context
from agent.contracts import ToolResult, tool_result_envelope, unwrap_tool_data
from agent.multi_agent import LangGraphVersionManager
from agent.planner import AssistantPlanner
from agent.registry import AGENT_REGISTRY, get_agent_definition
from agent.verifier import verify_assistant_response
from agent.workflow_planner import WorkflowPlanner
from agent.workflow_verifier import WorkflowVerifier


class AgentArchitectureTests(unittest.TestCase):
    def test_release_context_uses_explicit_output_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = build_release_context(
                team="SourceOne",
                release="7.2.11",
                role="QA Engineer",
                user="prakash",
                output_dir=tmp,
            )

            self.assertEqual(context.label, "SourceOne / 7.2.11")
            self.assertEqual(context.output_path("qa_validation.json"), Path(tmp) / "qa_validation.json")
            self.assertEqual(context.as_dict()["role"], "QA Engineer")

    def test_planner_answers_recommended_testcases_from_output_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            (output_dir / "testcase_impact.json").write_text(
                json.dumps(
                    {
                        "summary": {
                            "total_recommended_test_cases": 65,
                            "software_requiring_update": 17,
                            "software_with_test_coverage": 5,
                            "software_without_test_coverage": 12,
                        }
                    }
                ),
                encoding="utf-8",
            )
            context = build_release_context(team="SourceOne", release="7.2.11", output_dir=output_dir)
            result = AssistantPlanner(context).answer("how many test cases are recommended for SourceOne?")

            self.assertIsNotNone(result)
            self.assertTrue(result.success)
            self.assertIn("65 recommended QA test cases", result.message)
            self.assertIn("testcase_impact.json", result.paths["testcase_impact"])

    def test_registry_exposes_agent_tool_contract(self):
        definition = get_agent_definition("qa_validation")

        self.assertIn("generate_qa_validation", definition.allowed_tools)
        self.assertIn("testcase_impact_results", definition.produced_outputs)

    def test_workflow_planner_routes_to_first_missing_output(self):
        plan = WorkflowPlanner().plan({"software_inventory": [{"software_name": "OpenSSL"}]})

        self.assertEqual(plan.next_agent, "research")
        self.assertIn("latest_versions", plan.pending_outputs)

    def test_workflow_verifier_retries_once_then_fails_closed(self):
        verifier = WorkflowVerifier()
        first = verifier.verify({"last_agent": "qa_validation"})
        second = verifier.verify({"last_agent": "qa_validation", "verification_retries": first.retry_counts})

        self.assertEqual(first.next_agent, "qa_validation")
        self.assertEqual(second.next_agent, "end")
        self.assertIn("retry limit", second.reason)

    def test_verifier_warns_when_tool_response_has_no_source(self):
        context = build_release_context(team="SourceOne", release="7.2.11")
        result = ToolResult(success=True, source="", message="Done", source_type="app_tool")

        verification = verify_assistant_response(result, context)

        self.assertFalse(verification.passed)
        self.assertIn("missing tool source", verification.warnings)

    def test_tool_result_envelope_preserves_legacy_fields(self):
        result = tool_result_envelope(
            source="Used app tool: Example",
            data={"value": 1},
            value=1,
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["value"], 1)
        self.assertEqual(result["value"], 1)
        self.assertEqual(unwrap_tool_data(result)["value"], 1)

    def test_langgraph_workflow_carries_release_context_and_registry_tools(self):
        async def get_software_list(category="ALL"):
            return tool_result_envelope(source="test", data={"software": ["OpenSSL"], "category": category}, software=["OpenSSL"], category=category)

        async def query_server(software_name):
            return tool_result_envelope(source="test", data={"Build Version": "1.0", "source": "live server"}, **{"Build Version": "1.0"})

        async def extract_from_pdf(software_name):
            return tool_result_envelope(source="test", data={"Build Version": "1.0"}, **{"Build Version": "1.0"})

        async def search_latest_version(software_name, force_refresh=False):
            return tool_result_envelope(source="test", data={"Build Version": "1.1"}, **{"Build Version": "1.1"})

        async def compare_versions(latest=None, current=None):
            data = {"OpenSSL": {"current": {"Build Version": "1.0"}, "latest": {"Build Version": "1.1"}, "needs_update": True}}
            return tool_result_envelope(source="test", data=data, **data)

        async def get_run_history(software_name, limit=5):
            return tool_result_envelope(source="test", data={"history": []}, history=[])

        async def check_vulnerabilities(software_name, version=None, needs_update=False, force_refresh=False):
            return tool_result_envelope(source="test", data={"risk_level": "LOW"}, risk_level="LOW")

        async def save_vulnerability_report(vulnerabilities):
            return tool_result_envelope(source="test", data={"saved": True}, saved=True)

        async def assess_package_readiness(comparison=None, latest=None, vulnerabilities=None):
            data = {"package_readiness": {"OpenSSL": {"Readiness": "Ready"}}}
            return tool_result_envelope(source="test", data=data, **data)

        async def save_package_readiness(package_readiness):
            return tool_result_envelope(source="test", data={"saved": True}, saved=True)

        async def check_compatibility(comparison=None, package_readiness=None):
            data = {"compatibility": {"OpenSSL": {"Compatibility Status": "Compatible"}}}
            return tool_result_envelope(source="test", data=data, **data)

        async def generate_qa_validation(comparison=None, package_readiness=None):
            data = {"qa_validation": {"OpenSSL": {"Test Result": "PASS"}}}
            return tool_result_envelope(source="test", data=data, **data)

        async def save_qa_validation(qa_validation):
            return tool_result_envelope(source="test", data={"saved": True}, saved=True)

        async def generate_testcase_impact(comparison=None):
            data = {"testcase_impact": {"summary": {"total_recommended_test_cases": 3}}}
            return tool_result_envelope(source="test", data=data, **data)

        async def generate_excel_assessment():
            return tool_result_envelope(source="test", data={"path": "report.xlsx"}, path="report.xlsx")

        async def send_notification(report=None):
            return tool_result_envelope(source="test", data={"sent": True}, sent=True)

        async def log_audit_event(step, details=None):
            return tool_result_envelope(source="test", data={"logged": True}, logged=True)

        async def noop(**kwargs):
            return tool_result_envelope(source="test", data={}, **{})

        tools = {
            "get_software_list": get_software_list,
            "query_server": query_server,
            "extract_from_pdf": extract_from_pdf,
            "search_latest_version": search_latest_version,
            "compare_versions": compare_versions,
            "get_run_history": get_run_history,
            "check_vulnerabilities": check_vulnerabilities,
            "save_vulnerability_report": save_vulnerability_report,
            "assess_package_readiness": assess_package_readiness,
            "save_package_readiness": save_package_readiness,
            "check_compatibility": check_compatibility,
            "generate_qa_validation": generate_qa_validation,
            "save_qa_validation": save_qa_validation,
            "generate_testcase_impact": generate_testcase_impact,
            "generate_excel_assessment": generate_excel_assessment,
            "send_notification": send_notification,
            "log_audit_event": log_audit_event,
        }
        for definition in AGENT_REGISTRY.values():
            for tool in definition.allowed_tools:
                tools.setdefault(tool, noop)

        context = build_release_context(team="SourceOne", release="7.2.11")
        with patch("agent.multi_agent.init_db"), patch("agent.multi_agent.log_audit"), patch("agent.multi_agent.save_run_result"):
            result = asyncio.run(LangGraphVersionManager(tools, release_context=context).run("run workflow"))

        self.assertEqual(result["workflow_status"], "completed")
        self.assertEqual(result["release_context"]["team"], "SourceOne")
        self.assertEqual(result["testcase_impact_results"]["summary"]["total_recommended_test_cases"], 3)
        self.assertEqual(result["verification_result"]["passed"], True)

    def test_langgraph_workflow_fails_instead_of_looping_when_agent_output_missing(self):
        async def get_software_list(category="ALL"):
            return tool_result_envelope(source="test", data={"software": ["OpenSSL"]}, software=["OpenSSL"])

        async def query_server(software_name):
            return tool_result_envelope(source="test", data={"Build Version": "1.0"}, **{"Build Version": "1.0"})

        async def extract_from_pdf(software_name):
            return tool_result_envelope(source="test", data={"Build Version": "1.0"}, **{"Build Version": "1.0"})

        async def search_latest_version(software_name, force_refresh=False):
            return tool_result_envelope(source="test", data={}, **{})

        async def noop(**kwargs):
            return tool_result_envelope(source="test", data={}, **{})

        tools = {
            "get_software_list": get_software_list,
            "query_server": query_server,
            "extract_from_pdf": extract_from_pdf,
            "search_latest_version": search_latest_version,
        }
        for definition in AGENT_REGISTRY.values():
            for tool in definition.allowed_tools:
                tools.setdefault(tool, noop)

        with patch("agent.multi_agent.init_db"), patch("agent.multi_agent.log_audit"), patch("agent.multi_agent.save_run_result"):
            result = asyncio.run(LangGraphVersionManager(tools).run("run workflow"))

        self.assertEqual(result["workflow_status"], "failed")
        self.assertIn(2, result["verification_retries"].values())
        self.assertIn("retry limit", result["verification_result"]["reason"])


if __name__ == "__main__":
    unittest.main()
