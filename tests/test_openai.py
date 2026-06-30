import asyncio

from tests.conftest_path import PROJECT_ROOT  # noqa: F401
from Core.openai_client import OpenAIClient

SYSTEM_PROMPT = """You are an assistant that extracts software version info from search results.
Find the absolute latest Build Version and Cumulative Update (CU).

Output ONLY these two lines:
- Build Version: [value or 'Not Found']
- Cumulative Update (CU): [value or 'Not Found']"""


async def main():
    llm = OpenAIClient()
    raw = await llm.extract(
        system_prompt=SYSTEM_PROMPT,
        user_prompt="Software: SQL Server 2019\n\nSearch Results:\nCU32 (Latest), 15.0.4430.1, 2019.150.4430.1",
    )
    print("RAW OUTPUT:")
    print(repr(raw))


asyncio.run(main())

