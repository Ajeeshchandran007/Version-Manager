import unittest
from unittest.mock import patch

from tests.conftest_path import PROJECT_ROOT  # noqa: F401
from Core.assistant_tool_router import resolve_assistant_tool


class AssistantToolRouterTests(unittest.TestCase):
    def test_semantic_deterministic_routes_release_artifact_variations(self):
        prompts = (
            "show release artifacts for SourceOne",
            "what did workflow produce for SourceOne 7.2.11",
            "show evidence package",
            "which files were generated",
        )
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                decision = resolve_assistant_tool(prompt, role="Release Engineer", team="SourceOne", release="7.2.11", config={})
                self.assertTrue(decision.allowed)
                self.assertEqual(decision.selected_tool, "release_reports")

    def test_semantic_route_denies_qa_release_artifacts(self):
        decision = resolve_assistant_tool("where are release artifacts", role="QA Engineer", team="SourceOne", release="7.2.11", config={})

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.selected_tool, "release_reports")
        self.assertEqual(decision.source_label, "Access guardrail: QA role")

    def test_semantic_route_denies_qa_package_readiness(self):
        decision = resolve_assistant_tool(
            "what is ready for packaging in SourceOne",
            role="QA Engineer",
            team="SourceOne",
            release="7.2.11",
            config={},
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.selected_tool, "package_readiness")
        self.assertEqual(decision.source_label, "Access guardrail: QA role")

    def test_semantic_route_allows_release_package_readiness(self):
        decision = resolve_assistant_tool(
            "which packages are blocked",
            role="Release Engineer",
            team="SourceOne",
            release="7.2.11",
            config={},
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.selected_tool, "package_readiness")

    def test_embedding_route_is_optional_and_cached_path_configurable(self):
        config = {
            "openai_api_key": "test-key",
            "assistant_router": {
                "embedding_enabled": True,
                "embedding_threshold": 0.72,
            },
        }
        fake_rows = [
            {"tool": "release_reports", "text": "show generated outputs", "embedding": [1.0, 0.0]},
            {"tool": "package_readiness", "text": "package readiness", "embedding": [0.0, 1.0]},
        ]
        with patch("Core.assistant_tool_router._load_or_build_tool_embeddings", return_value=fake_rows), patch(
            "Core.assistant_tool_router._embed_texts", return_value=[[1.0, 0.0]]
        ):
            decision = resolve_assistant_tool(
                "display workflow deliverables",
                role="Release Engineer",
                team="SourceOne",
                release="7.2.11",
                config=config,
            )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.selected_tool, "release_reports")
        self.assertEqual(decision.method, "embedding")


if __name__ == "__main__":
    unittest.main()
