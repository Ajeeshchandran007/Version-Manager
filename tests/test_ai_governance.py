import unittest

from tests.conftest_path import PROJECT_ROOT  # noqa: F401
from Core.ai_governance import (
    EVIDENCE_FIRST_ORDER,
    WEB_SEARCH_ALLOWED_CATEGORIES,
    WEB_SEARCH_BLOCKED_CATEGORIES,
    apply_final_governance,
    classify_web_search_prompt,
    verify_final_assistant_response,
)


class AIGovernanceTests(unittest.TestCase):
    def test_evidence_first_order_is_formalized(self):
        self.assertEqual(
            EVIDENCE_FIRST_ORDER,
            ("generated_output", "mcp_app_tool", "approved_web_search", "ai_fallback"),
        )

    def test_web_search_policy_allows_only_product_categories(self):
        latest = classify_web_search_prompt("latest version of libcurl", known_software=["libCurl"])
        cve = classify_web_search_prompt("OpenSSL CVE security advisory")
        compatibility = classify_web_search_prompt("Intel i7 compatibility support matrix for SourceOne 7.2.11", team="SourceOne", release="7.2.11")

        self.assertTrue(latest.allowed)
        self.assertIn(latest.category, WEB_SEARCH_ALLOWED_CATEGORIES)
        self.assertTrue(cve.allowed)
        self.assertIn(cve.category, WEB_SEARCH_ALLOWED_CATEGORIES)
        self.assertTrue(compatibility.allowed)
        self.assertIn(compatibility.category, WEB_SEARCH_ALLOWED_CATEGORIES)

    def test_web_search_policy_blocks_general_people_and_internal_release_data(self):
        politics = classify_web_search_prompt("who is president of france")
        india_president = classify_web_search_prompt("who is the president of india")
        package = classify_web_search_prompt("what is ready for packaging in SourceOne", team="SourceOne", release="7.2.11")
        artifacts = classify_web_search_prompt("show the release artifacts for SourceOne 7.2.11", team="SourceOne", release="7.2.11")

        self.assertFalse(politics.allowed)
        self.assertIn(politics.category, WEB_SEARCH_BLOCKED_CATEGORIES)
        self.assertFalse(india_president.allowed)
        self.assertEqual(india_president.category, "people_news_politics")
        self.assertFalse(package.allowed)
        self.assertEqual(package.category, "internal_release_data")
        self.assertFalse(artifacts.allowed)
        self.assertEqual(artifacts.category, "internal_release_data")

    def test_final_verifier_blocks_qa_package_readiness_leak(self):
        verification = verify_final_assistant_response(
            prompt="what is ready for packaging in SourceOne",
            content="Package readiness summary for SourceOne.",
            source="Used MCP tool: Package Readiness",
            role="QA Engineer",
            team="SourceOne",
            release="7.2.11",
        )

        self.assertTrue(verification.blocked)
        self.assertIn("qa package access not blocked", verification.warnings)

    def test_final_governance_rewrites_blocked_qa_package_response(self):
        content, source, verification = apply_final_governance(
            prompt="what is ready for packaging in SourceOne",
            content="Package readiness summary for SourceOne.",
            source="Used MCP tool: Package Readiness",
            role="QA Engineer",
            team="SourceOne",
            release="7.2.11",
        )

        self.assertTrue(verification.blocked)
        self.assertEqual(source, "Access guardrail: AI governance")
        self.assertIn("not available in QA Assistant", content)

    def test_final_verifier_warns_when_ai_fallback_answers_project_fact(self):
        content, source, verification = apply_final_governance(
            prompt="what is current version of libcurl",
            content="The current version is 1.0.",
            source="Used AI fallback",
            role="Release Engineer",
            team="SourceOne",
            release="7.2.11",
        )

        self.assertFalse(verification.passed)
        self.assertEqual(source, "Used AI fallback")
        self.assertIn("Governance note", content)


if __name__ == "__main__":
    unittest.main()
