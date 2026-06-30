"""MCP client adapter used by the ReAct agent."""
import json
import sys
from contextlib import AsyncExitStack
from typing import Any, Callable

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


class MCPToolClient:
    """Connects to the VersionManager MCP server and exposes async callables."""

    def __init__(self, server_script: str = "mcp_server.py"):
        self.server_script = server_script
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def __aenter__(self):
        self._stack = AsyncExitStack()
        params = StdioServerParameters(
            command=sys.executable,
            args=["-u", self.server_script],
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._stack:
            await self._stack.aclose()

    async def list_tool_names(self) -> list[str]:
        if not self._session:
            raise RuntimeError("MCP session is not initialized")
        result = await self._session.list_tools()
        return [tool.name for tool in result.tools]

    def as_agent_tools(self, allowed_tools: list[str] | None = None) -> dict[str, Callable[..., Any]]:
        names = allowed_tools or [
            "get_software_list",
            "query_server",
            "extract_from_pdf",
            "search_latest_version",
            "compare_versions",
            "get_run_history",
            "check_vulnerabilities",
            "save_vulnerability_report",
            "assess_package_readiness",
            "save_package_readiness",
            "check_compatibility",
            "generate_qa_validation",
            "save_qa_validation",
            "generate_testcase_impact",
            "generate_excel_assessment",
            "send_notification",
            "log_audit_event",
        ]
        return {name: self._make_tool(name) for name in names}

    def _make_tool(self, tool_name: str):
        async def call_tool(**kwargs):
            if not self._session:
                raise RuntimeError("MCP session is not initialized")
            result = await self._session.call_tool(tool_name, kwargs or None)
            content = []
            for item in result.content:
                text = getattr(item, "text", None)
                if text is not None:
                    content.append(text)
                else:
                    content.append(str(item))
            text_result = "\n".join(content)
            try:
                return json.loads(text_result)
            except json.JSONDecodeError:
                return {"mcp_tool": tool_name, "content": text_result}

        return call_tool


def parse_mcp_text_result(result: dict) -> str:
    return json.dumps(result, indent=2)
