"""Run the LangGraph multi-agent workflow against the MCP tool layer."""
import asyncio

from agent.mcp_tool_client import MCPToolClient
from agent.multi_agent import LangGraphVersionManager
from agent.memory import init_db


async def main():
    init_db()
    async with MCPToolClient("mcp_server.py") as mcp_tools:
        tools = mcp_tools.as_agent_tools()
        workflow = LangGraphVersionManager(tools)
        summary = await workflow.run(
            "Use the MCP server tools to check software versions, assess security risk, and notify the team.",
            category="ALL",
        )
        print("\n=== MCP-backed Multi-Agent Summary ===")
        print(summary)


if __name__ == "__main__":
    asyncio.run(main())
