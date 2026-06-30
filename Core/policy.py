"""Approval and safety policy for side-effecting tools."""
import os

from dotenv import load_dotenv


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))


class PolicyError(PermissionError):
    pass


def require_approval(action: str, risk: str = "medium") -> None:
    """
    Enforce a simple approval gate for side effects.

    Set VERSION_MANAGER_AUTO_APPROVE=true for unattended lab/demo runs.
    Production deployments should replace this with RBAC/workflow approval.
    """
    approved = os.environ.get("VERSION_MANAGER_AUTO_APPROVE", "").lower()
    if approved in {"1", "true", "yes"}:
        return
    raise PolicyError(
        f"Approval required for action='{action}' risk='{risk}'. "
        "Set VERSION_MANAGER_AUTO_APPROVE=true only for approved unattended runs."
    )
