from tests.conftest_path import PROJECT_ROOT  # noqa: F401
from Utils.utils import load_config
from langchain_tavily import TavilySearch

config = load_config()
t = TavilySearch(max_results=2, tavily_api_key=config["tavily_api_key"])
results = t.invoke({"query": "SQL Server 2019 latest build version"})

print("type(results):", type(results))
if isinstance(results, list):
    print("type(results[0]):", type(results[0]))
    print("results[0]:", repr(results[0]))
else:
    print("results:", repr(results))

