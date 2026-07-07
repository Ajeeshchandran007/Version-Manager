"""Shared release context resolution for agents, tools, and assistants."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACES_DIR = PROJECT_ROOT / "workspaces"


@dataclass(frozen=True)
class ReleaseContext:
    team: str
    release: str
    role: str = ""
    user: str = ""
    run_id: str = ""
    category: str = "ALL"
    workspace_dir: Path = WORKSPACES_DIR
    output_dir: Path = PROJECT_ROOT / "output"

    @property
    def label(self) -> str:
        if self.team and self.release:
            return f"{self.team} / {self.release}"
        return self.team or self.release or "selected context"

    def output_path(self, filename: str) -> Path:
        return self.output_dir / filename

    def as_dict(self) -> dict[str, str]:
        return {
            "team": self.team,
            "release": self.release,
            "role": self.role,
            "user": self.user,
            "run_id": self.run_id,
            "category": self.category,
            "workspace_dir": str(self.workspace_dir),
            "output_dir": str(self.output_dir),
        }


def build_release_context(
    *,
    team: str = "",
    release: str = "",
    role: str = "",
    user: str = "",
    run_id: str = "",
    category: str = "ALL",
    output_dir: str | Path | None = None,
) -> ReleaseContext:
    resolved_team = team or _best_team_from_workspaces()
    resolved_release = release or _best_release_for_team(resolved_team)
    resolved_output_dir = Path(output_dir) if output_dir else _output_dir_for(resolved_team, resolved_release)
    return ReleaseContext(
        team=resolved_team,
        release=resolved_release,
        role=role,
        user=user,
        run_id=run_id,
        category=category,
        workspace_dir=WORKSPACES_DIR,
        output_dir=resolved_output_dir,
    )


def context_from_app(app_context: dict[str, Any]) -> ReleaseContext:
    return build_release_context(
        team=str(app_context.get("team") or ""),
        release=str(app_context.get("release") or ""),
        role=str(app_context.get("role") or ""),
        user=str(app_context.get("user") or ""),
        run_id=str(app_context.get("run_id") or ""),
        category=str(app_context.get("category") or "ALL"),
        output_dir=app_context.get("output_dir"),
    )


def workspace_release_candidates(prompt: str = "") -> list[tuple[int, str, str, Path]]:
    prompt_lower = prompt.lower()
    rows: list[tuple[int, str, str, Path]] = []
    if not WORKSPACES_DIR.exists():
        return rows
    for output_dir in WORKSPACES_DIR.glob("*/releases/*/output"):
        team = output_dir.parts[-4]
        release = output_dir.parts[-2]
        score = 0
        if team.lower() in prompt_lower:
            score += 10
        if release.lower() in prompt_lower:
            score += 6
        if any((output_dir / name).exists() for name in ("testcase_impact.json", "qa_validation.json", "qa_signoff.json")):
            score += 2
        rows.append((score, team, release, output_dir))
    return sorted(rows, key=lambda row: row[0], reverse=True)


def _output_dir_for(team: str, release: str) -> Path:
    if team and release:
        return WORKSPACES_DIR / team / "releases" / release / "output"
    if team:
        return WORKSPACES_DIR / team / "output"
    return PROJECT_ROOT / "output"


def _best_team_from_workspaces() -> str:
    candidates = workspace_release_candidates()
    return candidates[0][1] if candidates else ""


def _best_release_for_team(team: str) -> str:
    candidates = [row for row in workspace_release_candidates() if not team or row[1] == team]
    return candidates[0][2] if candidates else ""
