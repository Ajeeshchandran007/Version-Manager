"""Planner for role assistants: prefer app/MCP data before AI fallback."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.context import ReleaseContext, workspace_release_candidates
from agent.contracts import AssistantPlan, ToolResult


class AssistantPlanner:
    def __init__(self, context: ReleaseContext):
        self.context = context

    def plan(self, prompt: str) -> AssistantPlan:
        prompt_lower = prompt.lower()
        if _tested_by_requested(prompt_lower):
            return AssistantPlan("qa_testers", "app_tool", "QA Tester Details", reason="Tester details are local QA data.")
        if _current_release_requested(prompt_lower):
            return AssistantPlan("release_context", "app_tool", "Release Context", reason="Release context is known by the app.")
        if _recommended_testcase_requested(prompt_lower):
            return AssistantPlan("testcase_impact", "app_tool", "Test Case Impact", reason="Recommended test cases are generated artifacts.")
        if _qa_widget_requested(prompt_lower):
            return AssistantPlan("qa_dashboard", "app_tool", "QA Validation", reason="QA dashboard is generated from current QA rows.")
        return AssistantPlan("general", "ai_fallback", "AI fallback", needs_ai_fallback=True, reason="No deterministic tool matched.")

    def answer(self, prompt: str, qa_records: list[dict[str, Any]] | None = None) -> ToolResult | None:
        plan = self.plan(prompt)
        if plan.needs_ai_fallback:
            return None
        if plan.intent == "qa_testers":
            return self._answer_tested_by(prompt, qa_records or [])
        if plan.intent == "release_context":
            return self._answer_current_release(prompt)
        if plan.intent == "testcase_impact":
            return self._answer_recommended_testcases(prompt)
        if plan.intent == "qa_dashboard":
            return ToolResult(
                success=True,
                source=f"Used app tool: {plan.tool_name}",
                message="I prepared a compact QA dashboard snapshot from the current release data.",
                data={"context": self.context.as_dict()},
                widget="qa_dashboard",
            )
        return None

    def _answer_tested_by(self, prompt: str, qa_records: list[dict[str, Any]]) -> ToolResult:
        if not qa_records:
            return ToolResult(
                success=False,
                source="Used app tool: QA Tester Details",
                message="I do not have QA validation rows for this team/release yet. Run the QA workflow or load QA validation data first.",
                data={"context": self.context.as_dict()},
                errors=["qa_validation_empty"],
            )

        prompt_lower = prompt.lower()
        rows = qa_records
        matching = [
            row for row in qa_records
            if str(row.get("Software Name") or "").strip().lower()
            and str(row.get("Software Name") or "").strip().lower() in prompt_lower
        ]
        if matching:
            rows = matching

        lines = []
        for row in rows[:20]:
            software = str(row.get("Software Name") or "Unknown software").strip()
            tested_by = str(row.get("Tested By") or "").strip() or "Not recorded"
            result = str(row.get("Test Result") or "").strip() or "Not tested"
            lines.append(f"- **{software}**: {tested_by} ({result})")

        return ToolResult(
            success=True,
            source="Used app tool: QA Tester Details",
            message=f"Tester details for **{self.context.label}**:\n\n" + "\n".join(lines),
            data={"context": self.context.as_dict(), "rows": rows[:20]},
        )

    def _answer_current_release(self, prompt: str) -> ToolResult:
        if self.context.team and self.context.release:
            message = f"The active UI context is **{self.context.label}**."
            return ToolResult(
                success=True,
                source="Used app tool: Release Context",
                message=message,
                data={"context": self.context.as_dict()},
                paths={"output_dir": str(self.context.output_dir)},
            )

        candidates = workspace_release_candidates(prompt)
        if not candidates:
            return ToolResult(
                success=False,
                source="Used app tool: Release Context",
                message="I could not find an active release in the UI or generated workspace outputs.",
                errors=["release_context_missing"],
            )

        _, team, release, output_dir = candidates[0]
        others = ", ".join(f"{row[1]} / {row[2]}" for row in candidates[1:4])
        message = f"I found **{team} / {release}** as the best matching generated release context."
        if others:
            message += f" Other available contexts: {others}."
        return ToolResult(
            success=True,
            source="Used app tool: Release Context",
            message=message,
            data={"team": team, "release": release},
            paths={"output_dir": str(output_dir)},
        )

    def _answer_recommended_testcases(self, prompt: str) -> ToolResult:
        impact, source_path = self._load_best_output_json("testcase_impact.json", prompt)
        summary = impact.get("summary") if isinstance(impact.get("summary"), dict) else {}
        if not summary:
            return ToolResult(
                success=False,
                source="Used app tool: Test Case Impact",
                message=(
                    "I checked the active workspace outputs, but I could not find a generated Test Case Impact summary "
                    "for this context. Confirm the selected team/release or run the QA workflow again."
                ),
                data={"context": self.context.as_dict()},
                errors=["testcase_impact_missing"],
            )

        total = int(summary.get("total_recommended_test_cases") or 0)
        requiring_update = int(summary.get("software_requiring_update") or 0)
        with_coverage = int(summary.get("software_with_test_coverage") or 0)
        without_coverage = int(summary.get("software_without_test_coverage") or 0)
        message = (
            f"The current release has **{total} recommended QA test cases** mapped from the test case repository.\n\n"
            f"{requiring_update} software item(s) require update review. "
            f"{with_coverage} have mapped test coverage, and {without_coverage} do not yet have mapped repository coverage.\n\n"
            f"Source: `{source_path}`"
        )
        return ToolResult(
            success=True,
            source="Used app tool: Test Case Impact",
            message=message,
            data={"context": self.context.as_dict(), "summary": summary},
            paths={"testcase_impact": source_path},
        )

    def _load_best_output_json(self, filename: str, prompt: str) -> tuple[dict[str, Any], str]:
        for path in self._candidate_output_paths(filename, prompt):
            data = _load_json_file(path)
            if data:
                return data, str(path)
        return {}, ""

    def _candidate_output_paths(self, filename: str, prompt: str) -> list[Path]:
        prompt_lower = prompt.lower()
        candidates = [self.context.output_path(filename)]
        for _, team, release, output_dir in workspace_release_candidates(prompt):
            path = output_dir / filename
            if team.lower() in prompt_lower or release.lower() in prompt_lower:
                candidates.insert(0, path)
            else:
                candidates.append(path)

        deduped: list[Path] = []
        seen = set()
        for path in candidates:
            key = str(path).lower()
            if key not in seen:
                seen.add(key)
                deduped.append(path)
        return deduped


def _load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _qa_widget_requested(prompt_lower: str) -> bool:
    return any(term in prompt_lower for term in ("qa dashboard", "qa status", "qa summary", "signoff", "sign-off", "test status"))


def _recommended_testcase_requested(prompt_lower: str) -> bool:
    return "test case" in prompt_lower and any(term in prompt_lower for term in ("recommend", "recommended", "how many", "count", "total"))


def _current_release_requested(prompt_lower: str) -> bool:
    return "release" in prompt_lower and any(term in prompt_lower for term in ("current", "active", "selected", "which", "what"))


def _tested_by_requested(prompt_lower: str) -> bool:
    return any(
        term in prompt_lower
        for term in ("who tested", "tested by", "tester", "who validated", "validated by", "who executed")
    )
