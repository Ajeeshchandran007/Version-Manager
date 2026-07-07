import unittest
import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest_path import PROJECT_ROOT  # noqa: F401
from App.assistant_chat import (
    _answer_current_from_outputs,
    _answer_latest_from_outputs,
    _format_web_search_answer,
    _current_version_mcp_answer,
    _latest_version_research_answer,
    _web_search_answer,
)


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


if __name__ == "__main__":
    unittest.main()
