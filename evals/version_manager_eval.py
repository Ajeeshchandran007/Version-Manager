"""Offline evaluation harness for the Version Manager agent design."""
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Core.comparator import compare
from Utils.parse_version import parse_version_text


EVAL_CASES = [
    {
        "test_name": "latest_version_extraction",
        "software_name": "SQL Server 2019",
        "description": "Parser extracts SQL Server build and CU from model output.",
        "kind": "parse",
        "input": "- Build Version: 15.0.4430.1\n- Cumulative Update (CU): CU32",
        "expected": {"Build Version": "15.0.4430.1", "Cumulative Update (CU)": "CU32"},
    },
    {
        "test_name": "known_mismatch_requires_update",
        "software_name": "libCurl",
        "description": "A known latest/current build mismatch is flagged as needing update.",
        "kind": "compare",
        "latest": {"libCurl": {"Build Version": "8.20.0", "Cumulative Update (CU)": None}},
        "current": {"libCurl": {"Build Version": "8.13.0", "Cumulative Update (CU)": None}},
        "expected": {"needs_update": True, "unknown": False},
    },
    {
        "test_name": "unknown_is_not_up_to_date",
        "software_name": "ElasticSearch",
        "description": "Missing latest/current data is marked unknown instead of up to date.",
        "kind": "compare",
        "latest": {"ElasticSearch": {"Build Version": None, "Cumulative Update (CU)": None}},
        "current": {"ElasticSearch": {"Build Version": None, "Cumulative Update (CU)": None}},
        "expected": {"needs_update": False, "unknown": True},
    },
]


async def run_evals() -> dict:
    results = []
    for case in EVAL_CASES:
        if case["kind"] == "parse":
            actual = parse_version_text(case["input"])
            passed = actual == case["expected"]
        elif case["kind"] == "compare":
            report = compare(case["latest"], case["current"])
            actual = report[case["software_name"]]
            passed = all(actual.get(k) == v for k, v in case["expected"].items())
        else:
            actual = {"error": f"Unknown eval kind: {case['kind']}"}
            passed = False

        results.append({
            "test_name": case["test_name"],
            "software_name": case["software_name"],
            "description": case["description"],
            "passed": passed,
            "actual": actual,
            "expected": case["expected"],
        })

    summary = {
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "failed": [r for r in results if not r["passed"]],
        "results": results,
    }
    out = Path("output/eval_results.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_evals()), indent=2))
