import unittest
import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from tests.conftest_path import PROJECT_ROOT  # noqa: F401
from App.assistant_chat import (
    _answer_current_from_outputs,
    _answer_latest_from_outputs,
    _answer_package_readiness_from_outputs,
    _answer_reports_from_outputs,
    _answer_vulnerability_from_outputs,
    _format_web_search_answer,
    _current_version_mcp_answer,
    _latest_version_research_answer,
    _tool_first_answer,
    _web_search_allowed,
    _web_search_answer,
)


def route_prompt_for_test(prompt: str) -> str:
    tool_answer = _tool_first_answer(prompt, pd.DataFrame())
    if tool_answer:
        return tool_answer["source"]
    current_answer, _ = asyncio.run(_current_version_mcp_answer(prompt))
    if current_answer:
        return current_answer["source"]
    latest_answer, _ = asyncio.run(_latest_version_research_answer(prompt))
    if latest_answer:
        return latest_answer["source"]
    allowed, _ = _web_search_allowed(prompt)
    return "Would use web search" if allowed else "Web search blocked"


class AssistantWebSearchTests(unittest.TestCase):
    def test_web_search_reports_missing_tavily_key(self):
        with patch("App.assistant_chat.load_config", return_value={"tavily_api_key": "${TAVILY_API_KEY}"}):
            answer, reason = _web_search_answer("intel i7 processor information")

        self.assertIsNone(answer)
        self.assertIn("Web search is not configured", reason)

    def test_web_search_formats_tavily_results(self):
        client = MagicMock()
        client.search.return_value = {
            "answer": "Intel Core i7 is a family of higher-performance Intel Core processors.",
            "results": [
                {
                    "title": "Intel Core i7",
                    "url": "https://www.intel.com/",
                    "content": "Core i7 processors are commonly used in laptops and desktops.",
                }
            ],
        }
        with patch("App.assistant_chat.load_config", return_value={"tavily_api_key": "test-key"}), patch(
            "App.assistant_chat.TavilyClient", return_value=client
        ):
            answer, reason = _web_search_answer("intel i7 processor information")

        self.assertEqual(reason, "")
        self.assertEqual(answer["source"], "Used web search")
        self.assertIn("Intel Core i7", answer["content"])
        self.assertIn("Sources:", answer["content"])

    def test_web_search_formatter_returns_empty_without_results(self):
        self.assertEqual(_format_web_search_answer("anything", {"results": []}), "")

    def test_web_search_guardrail_blocks_general_internet_questions(self):
        with patch("App.assistant_chat._software_names_from_inputs_and_outputs", return_value=["libCurl"]), patch(
            "App.assistant_chat.active_team_name", return_value="SourceOne"
        ), patch("App.assistant_chat.active_release_line", return_value="7.2.11"):
            allowed, reason = _web_search_allowed("who is president of france")

        self.assertFalse(allowed)
        self.assertIn("guardrail", reason.lower())

    def test_web_search_guardrail_allows_product_questions(self):
        with patch("App.assistant_chat._software_names_from_inputs_and_outputs", return_value=["libCurl"]), patch(
            "App.assistant_chat.active_team_name", return_value="SourceOne"
        ), patch("App.assistant_chat.active_release_line", return_value="7.2.11"):
            software_allowed, _ = _web_search_allowed("latest version of libcurl")
            compatibility_allowed, _ = _web_search_allowed("intel i7 processor compatibility requirement for SourceOne 7.2.11")

        self.assertTrue(software_allowed)
        self.assertTrue(compatibility_allowed)

    def test_web_search_guardrail_blocks_internal_package_questions(self):
        with patch("App.assistant_chat._software_names_from_inputs_and_outputs", return_value=["libCurl"]), patch(
            "App.assistant_chat.active_team_name", return_value="SourceOne"
        ), patch("App.assistant_chat.active_release_line", return_value="7.2.11"):
            allowed, reason = _web_search_allowed("What is ready for packaging in SourceOne")

        self.assertFalse(allowed)
        self.assertIn("Package readiness is internal release data", reason)

    def test_latest_version_question_uses_output_artifact_first(self):
        def fake_load(filename, prompt):
            if filename == "latest_versions.json":
                return {"libCurl": {"Build Version": "8.13.0", "Cumulative Update (CU)": "Not Found"}}, "latest_versions.json"
            return {}, ""

        with patch("App.assistant_chat._load_best_output_json", side_effect=fake_load), patch(
            "App.assistant_chat.active_team_name", return_value="SourceOne"
        ), patch("App.assistant_chat.active_release_line", return_value="7.2.11"):
            answer = _answer_latest_from_outputs("find latest version of libcurl for sourceone 7.2.11")

        self.assertEqual(answer["source"], "Used MCP tool: Latest Version Output")
        self.assertIn("8.13.0", answer["content"])

    def test_specific_latest_version_question_does_not_return_summary(self):
        def fake_load(filename, prompt):
            if filename == "latest_versions.json":
                return {
                    "libCurl": {"Build Version": "8.21.0"},
                    "OpenSSL": {"Build Version": "4.0.1"},
                }, "latest_versions.json"
            return {}, ""

        with patch("App.assistant_chat._load_best_output_json", side_effect=fake_load), patch(
            "App.assistant_chat.active_team_name", return_value="DPS"
        ), patch("App.assistant_chat.active_release_line", return_value="7.2.12"):
            answer = _answer_latest_from_outputs("libcurl latest version")

        self.assertEqual(answer["source"], "Used MCP tool: Latest Version Output")
        self.assertIn("8.21.0", answer["content"])
        self.assertNotIn("OpenSSL", answer["content"])

    def test_unknown_specific_latest_version_falls_through_to_mcp_or_web(self):
        def fake_load(filename, prompt):
            if filename == "latest_versions.json":
                return {"libCurl": {"Build Version": "8.21.0"}}, "latest_versions.json"
            return {}, ""

        with patch("App.assistant_chat._load_best_output_json", side_effect=fake_load), patch(
            "App.assistant_chat.active_team_name", return_value="DPS"
        ), patch("App.assistant_chat.active_release_line", return_value="7.2.12"):
            answer = _answer_latest_from_outputs("chrome latest version")

        self.assertIsNone(answer)

    def test_latest_version_uses_exact_prompt_release_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            sourceone_output = workspace / "SourceOne" / "releases" / "7.2.11" / "output"
            dps_output = workspace / "DPS" / "releases" / "16.12.05" / "output"
            sourceone_output.mkdir(parents=True)
            dps_output.mkdir(parents=True)
            (sourceone_output / "latest_versions.json").write_text(
                json.dumps({"libCurl": {"Build Version": "8.13.0"}}),
                encoding="utf-8",
            )
            (dps_output / "latest_versions.json").write_text(
                json.dumps({"libCurl": {"Build Version": "9.99.0"}}),
                encoding="utf-8",
            )

            with patch("App.assistant_chat.WORKSPACES_DIR", workspace), patch(
                "App.assistant_chat.active_output_path", return_value=dps_output / "latest_versions.json"
            ), patch("App.assistant_chat.active_team_name", return_value="DPS"), patch(
                "App.assistant_chat.active_release_line", return_value="16.12.05"
            ):
                answer = _answer_latest_from_outputs("find latest version of libcurl for SourceOne 7.2.11")

        self.assertIn("8.13.0", answer["content"])
        self.assertNotIn("9.99.0", answer["content"])

    def test_latest_release_question_without_software_summarizes_output(self):
        def fake_load(filename, prompt):
            if filename == "latest_versions.json":
                return {
                    "libCurl": {"Build Version": "8.13.0"},
                    "OpenSSL": {"Build Version": "3.5.1"},
                }, "workspaces/SourceOne/releases/7.2.11/output/latest_versions.json"
            return {}, ""

        with patch("App.assistant_chat._load_best_output_json", side_effect=fake_load):
            answer = _answer_latest_from_outputs("can you provide latest software version used in sourceone 7.2.11")

        self.assertEqual(answer["source"], "Used MCP tool: Latest Version Output")
        self.assertIn("SourceOne / 7.2.11", answer["content"])
        self.assertIn("libCurl", answer["content"])
        self.assertIn("OpenSSL", answer["content"])

    def test_current_version_question_uses_output_artifact_first(self):
        def fake_load(filename, prompt):
            if filename == "current_versions.json":
                return {"libCurl": {"Build Version": "8.21.0", "source": "PDF fallback"}}, "current_versions.json"
            return {}, ""

        with patch("App.assistant_chat._load_best_output_json", side_effect=fake_load), patch(
            "App.assistant_chat.active_team_name", return_value="SourceOne"
        ), patch("App.assistant_chat.active_release_line", return_value="7.2.11"):
            answer = _answer_current_from_outputs("what is current version of libcurl")

        self.assertEqual(answer["source"], "Used MCP tool: Current Version Output")
        self.assertIn("8.21.0", answer["content"])
        self.assertIn("PDF fallback", answer["content"])

    def test_latest_version_research_uses_mcp_before_web_search(self):
        fetcher = MagicMock()
        fetcher.fetch = AsyncMock(return_value={"Build Version": "8.13.0", "Cumulative Update (CU)": "Not Found"})
        with patch("App.assistant_chat._software_names_from_inputs_and_outputs", return_value=["libCurl"]), patch(
            "App.assistant_chat.VersionFetcher", return_value=fetcher
        ):
            answer, reason = asyncio.run(_latest_version_research_answer("latest version of libcurl"))

        self.assertEqual(reason, "")
        self.assertEqual(answer["source"], "Used MCP tool: Latest Version Research")
        self.assertIn("8.13.0", answer["content"])

    def test_current_version_uses_mcp_pdf_before_web_search(self):
        server = MagicMock()
        server.fetch = AsyncMock(return_value=None)
        pdf = MagicMock()
        pdf.fetch = AsyncMock(return_value={"Build Version": "8.21.0", "source": "PDF fallback"})

        with patch("App.assistant_chat._software_names_from_inputs_and_outputs", return_value=["libCurl"]), patch(
            "App.assistant_chat.active_team_name", return_value="SourceOne"
        ), patch("App.assistant_chat.active_release_line", return_value="7.2.11"), patch(
            "App.assistant_chat.load_config", return_value={}
        ), patch("App.assistant_chat.active_config", return_value={}), patch(
            "App.assistant_chat.ServerQuerier", return_value=server
        ), patch("App.assistant_chat.PDFReader", return_value=pdf):
            answer, reason = asyncio.run(_current_version_mcp_answer("what is current version of libcurl"))

        self.assertEqual(reason, "")
        self.assertEqual(answer["source"], "Used MCP tool: PDF Version Fallback")
        self.assertIn("8.21.0", answer["content"])

    def test_testcase_coverage_question_uses_mcp_tool(self):
        def fake_load(filename, prompt):
            if filename == "testcase_impact.json":
                return {
                    "summary": {
                        "software_with_test_coverage": 1,
                        "software_without_test_coverage": 1,
                        "total_recommended_test_cases": 2,
                    },
                    "impacted_software": {
                        "OpenSSL": {"Test Case Count": 2},
                        "libCurl": {"Test Case Count": 0},
                    },
                }, "testcase_impact.json"
            return {}, ""

        with patch("App.assistant_chat._load_best_output_json", side_effect=fake_load), patch(
            "App.assistant_chat.active_team_name", return_value="SourceOne"
        ), patch("App.assistant_chat.active_release_line", return_value="7.2.11"), patch(
            "App.assistant_chat.current_role", return_value="QA Engineer"
        ), patch("App.assistant_chat.current_user", return_value={"username": "qa"}), patch(
            "App.assistant_chat.active_output_path", return_value=Path("output")
        ):
            answer = _tool_first_answer("tell me which software has no testcase coverage.", pd.DataFrame())

        self.assertEqual(answer["source"], "Used MCP tool: Test Case Impact")
        self.assertIn("libCurl", answer["content"])

    def test_package_readiness_question_uses_output_artifact(self):
        def fake_load(filename, prompt):
            if filename == "package_readiness.json":
                return {
                    "libCurl": {
                        "Package Readiness": "Blocked",
                        "Upgrade Impact": "High",
                        "Owner": "Release Team",
                        "Blocker": "Vendor installer validation pending",
                    }
                }, "package_readiness.json"
            return {}, ""

        with patch("App.assistant_chat._load_best_output_json", side_effect=fake_load), patch(
            "App.assistant_chat.current_role", return_value="Release Engineer"
        ):
            answer = _answer_package_readiness_from_outputs("package readiness for libcurl")

        self.assertEqual(answer["source"], "Used MCP tool: Package Readiness")
        self.assertIn("Blocked", answer["content"])
        self.assertIn("Vendor installer validation pending", answer["content"])

    def test_release_pending_checklist_uses_package_readiness_before_web_search(self):
        def fake_load(filename, prompt):
            if filename == "package_readiness.json":
                return {
                    "libCurl": {
                        "Package Readiness": "Vendor Patch Available",
                        "Upgrade Impact": "High",
                        "Owner": "Release Team",
                        "Blocker": "Checksum, signature, test install, rollback validation, and approval pending",
                    }
                }, "package_readiness.json"
            return {}, ""

        with patch("App.assistant_chat._load_best_output_json", side_effect=fake_load), patch(
            "App.assistant_chat.active_team_name", return_value="SourceOne"
        ), patch("App.assistant_chat.active_release_line", return_value="7.2.11"), patch(
            "App.assistant_chat.current_role", return_value="Release Engineer"
        ), patch("App.assistant_chat.current_user", return_value={"username": "release"}), patch(
            "App.assistant_chat.active_output_path", return_value=Path("output")
        ):
            answer = _tool_first_answer("What checklist is pending?", pd.DataFrame())

        self.assertEqual(answer["source"], "Used MCP tool: Package Readiness")
        self.assertIn("Vendor Patch Available", answer["content"])
        self.assertIn("approval pending", answer["content"])

    def test_qa_package_readiness_is_denied_before_web_search(self):
        with patch("App.assistant_chat._load_best_output_json", return_value=({}, "")), patch(
            "App.assistant_chat.active_team_name", return_value="SourceOne"
        ), patch("App.assistant_chat.active_release_line", return_value="7.2.11"), patch(
            "App.assistant_chat.current_role", return_value="QA Engineer"
        ), patch("App.assistant_chat.current_user", return_value={"username": "qa"}), patch(
            "App.assistant_chat.active_output_path", return_value=Path("output")
        ):
            answer = _tool_first_answer("what is ready for packaging in sourceone", pd.DataFrame())

        self.assertEqual(answer["source"], "Access guardrail: QA role")
        self.assertIn("not available in QA Assistant", answer["content"])
        self.assertIn("Release Assistant", answer["content"])

    def test_vulnerability_question_uses_output_artifact(self):
        def fake_load(filename, prompt):
            if filename == "vulnerability_report.json":
                return {
                    "OpenSSL": {
                        "risk_level": "HIGH",
                        "severity": "CRITICAL",
                        "cves": [{"id": "CVE-2026-0001"}],
                        "assessment": "Security update required.",
                    }
                }, "vulnerability_report.json"
            return {}, ""

        with patch("App.assistant_chat._load_best_output_json", side_effect=fake_load):
            answer = _answer_vulnerability_from_outputs("OpenSSL vulnerability risk")

        self.assertEqual(answer["source"], "Used MCP tool: Vulnerability Assessment")
        self.assertIn("HIGH", answer["content"])
        self.assertIn("1 CVE", answer["content"])

    def test_reports_question_lists_generated_output_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            (output_dir / "latest_versions.json").write_text("{}", encoding="utf-8")
            (output_dir / "qa_validation.json").write_text("{}", encoding="utf-8")
            with patch("App.assistant_chat.active_output_path", return_value=output_dir / "__placeholder__"), patch(
                "App.assistant_chat.active_team_name", return_value="SourceOne"
            ), patch("App.assistant_chat.active_release_line", return_value="7.2.11"):
                answer = _answer_reports_from_outputs("what reports are available")

        self.assertEqual(answer["source"], "Used MCP tool: Release Reports")
        self.assertIn("Latest Versions", answer["content"])
        self.assertIn("QA Validation", answer["content"])

    def test_release_artifacts_question_lists_generated_output_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            (output_dir / "comparison_report.json").write_text("{}", encoding="utf-8")
            with patch("App.assistant_chat.active_output_path", return_value=output_dir / "__placeholder__"), patch(
                "App.assistant_chat.active_team_name", return_value="SourceOne"
            ), patch("App.assistant_chat.active_release_line", return_value="7.2.11"):
                with patch("App.assistant_chat.current_role", return_value="Release Engineer"), patch(
                    "App.assistant_chat.current_user", return_value={"username": "release"}
                ):
                    answer = _tool_first_answer("show the release artifacts for SourceOne 7.2.11", pd.DataFrame())

        self.assertEqual(answer["source"], "Used MCP tool: Release Reports")
        self.assertIn("Version Comparison", answer["content"])

    def test_prompt_routing_matrix_prefers_mcp_before_web(self):
        def fake_load(filename, prompt):
            if filename == "latest_versions.json":
                return {
                    "libCurl": {"Build Version": "8.13.0"},
                    "OpenSSL": {"Build Version": "3.5.1"},
                }, "workspaces/SourceOne/releases/7.2.11/output/latest_versions.json"
            if filename == "current_versions.json":
                return {"libCurl": {"Build Version": "8.21.0", "source": "PDF fallback"}}, "current_versions.json"
            if filename == "testcase_impact.json":
                return {
                    "summary": {"software_with_test_coverage": 1, "software_without_test_coverage": 1, "total_recommended_test_cases": 2},
                    "impacted_software": {"libCurl": {"Test Case Count": 0}},
                }, "testcase_impact.json"
            if filename == "package_readiness.json":
                return {"libCurl": {"Package Readiness": "Blocked", "Upgrade Impact": "High"}}, "package_readiness.json"
            if filename == "vulnerability_report.json":
                return {"OpenSSL": {"risk_level": "HIGH", "severity": "CRITICAL", "cves": [{"id": "CVE-1"}]}}, "vulnerability_report.json"
            return {}, ""

        prompts = {
            "what is current version of libcurl": "Used MCP tool: Current Version Output",
            "what is deployed version of libcurl": "Used MCP tool: Current Version Output",
            "find latest version of libcurl for SourceOne 7.2.11": "Used MCP tool: Latest Version Output",
            "can you provide latest software version used in sourceone 7.2.11": "Used MCP tool: Latest Version Output",
            "tell me which software has no testcase coverage.": "Used MCP tool: Test Case Impact",
            "how many recommended test cases are available": "Used MCP tool: Test Case Impact",
            "package readiness for libcurl": "Access guardrail: QA role",
            "what is ready for packaging in sourceone": "Access guardrail: QA role",
            "OpenSSL vulnerability risk": "Used MCP tool: Vulnerability Assessment",
            "what is current release": "Used MCP tool: Release Context",
            "show QA dashboard summary": "Used MCP tool: QA Validation",
            "show the release artifacts for SourceOne 7.2.11": "Access guardrail: QA role",
            "who is president of france": "Web search blocked",
            "intel i7 processor compatibility requirement for SourceOne 7.2.11": "Would use web search",
        }

        with patch("App.assistant_chat._load_best_output_json", side_effect=fake_load), patch(
            "App.assistant_chat.active_team_name", return_value="SourceOne"
        ), patch("App.assistant_chat.active_release_line", return_value="7.2.11"), patch(
            "App.assistant_chat.current_role", return_value="QA Engineer"
        ), patch("App.assistant_chat.current_user", return_value={"username": "qa"}), patch(
            "App.assistant_chat.active_output_path", return_value=Path("output")
        ), patch("App.assistant_chat._software_names_from_inputs_and_outputs", return_value=["libCurl", "OpenSSL"]):
            for prompt, expected_source in prompts.items():
                with self.subTest(prompt=prompt):
                    self.assertEqual(route_prompt_for_test(prompt), expected_source)


if __name__ == "__main__":
    unittest.main()
