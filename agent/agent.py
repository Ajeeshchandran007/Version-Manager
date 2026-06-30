# agent/agent.py
"""
ReAct agent orchestrator.
The agent is the only place that calls tools in sequence —
mcp_server.py exposes atomic tools; this file does the reasoning.
"""
import json
import uuid
import asyncio
import datetime
from openai import AsyncOpenAI
from Utils.utils import logger, load_config
from agent.memory import (
    init_db, log_audit, log_failure,
    save_run_result, get_baseline, get_recent_failures,
)
from agent.prompts import SYSTEM_PROMPT


class VersionManagerAgent:
    """
    Drives the full version-check pipeline using a ReAct loop.
    Tools are injected as async callables — no MCP coupling here,
    making this independently testable.
    """

    MAX_STEPS = 40          # hard cap — prevents infinite loops
    MODEL     = None        # resolved from config at init

    def __init__(self, tools: dict[str, callable], run_id: str | None = None):
        """
        Args:
            tools: mapping of tool_name → async callable
                   e.g. {"get_software_list": get_software_list_fn, ...}
            run_id: optional — auto-generated if not provided
        """
        config      = load_config()
        self.tools  = tools
        self.run_id = run_id or str(uuid.uuid4())[:8]
        self.model  = config.get("openai_model_name", "gpt-4o-mini")
        self.client = AsyncOpenAI(api_key=config["openai_api_key"])
        init_db()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    async def run(self, goal: str, category: str = "ALL") -> dict:
        """
        Execute the agent loop until MAX_STEPS or a FINAL ANSWER.

        Returns a summary dict:
            { run_id, category, steps_taken, needs_update, email_sent }
        """
        logger.info(f"[{self.run_id}] Agent started. goal='{goal}' category='{category}'")
        log_audit(self.run_id, "agent_start", "agent",
                  {"goal": goal, "category": category})

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",
             "content": f"Goal: {goal}\nCategory: {category}\nRun ID: {self.run_id}"},
        ]

        tool_schemas = self._build_tool_schemas()
        steps        = 0
        summary      = {"run_id": self.run_id, "category": category,
                        "steps_taken": 0, "needs_update": [], "email_sent": False}
        comparison_report: dict[str, dict] = {}

        while steps < self.MAX_STEPS:
            steps += 1
            logger.info(f"[{self.run_id}] Step {steps}/{self.MAX_STEPS}")

            # ── LLM turn ──────────────────────────────────────────────
            response = await self.client.chat.completions.create(
                model=self.model,
                temperature=0,
                tools=tool_schemas,
                tool_choice="auto",
                messages=messages,
            )

            msg = response.choices[0].message
            messages.append(msg)  # keep full history for context

            # ── FINAL ANSWER (no tool call) ────────────────────────────
            if not msg.tool_calls:
                content = msg.content or ""
                logger.info(f"[{self.run_id}] Final answer:\n{content}")
                log_audit(self.run_id, "final_answer", "agent", {"content": content})
                summary["steps_taken"] = steps
                summary["final_answer"] = content
                summary["comparison_report"] = comparison_report
                return summary

            # ── TOOL CALL(S) ───────────────────────────────────────────
            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}

                logger.info(f"[{self.run_id}] Tool call: {name}({args})")
                log_audit(self.run_id, "tool_call", f"tool:{name}", args)

                if name not in self.tools:
                    result = {"error": f"Unknown tool: {name}"}
                else:
                    try:
                        result = await self.tools[name](**args)
                        # Track email + update summary fields
                        if name == "send_notification":
                            summary["email_sent"] = result.get("sent", False)
                        if name == "compare_versions":
                            sw = args.get("software_name", "")
                            if result.get("needs_update") and sw:
                                summary["needs_update"].append(sw)
                            if sw:
                                comparison_report[sw] = result
                                current = result.get("current", {}) or {}
                                save_run_result(
                                    run_id=self.run_id,
                                    software=sw,
                                    category=category,
                                    build_ver=current.get("Build Version"),
                                    cu_ver=current.get("Cumulative Update (CU)"),
                                    source=result.get("current_source", "unknown"),
                                    needs_update=bool(result.get("needs_update")),
                                )
                    except Exception as e:
                        logger.error(f"[{self.run_id}] Tool error {name}: {e}")
                        log_failure(
                            args.get("software_name", name),
                            args.get("host"),
                            str(e),
                        )
                        result = {"error": str(e)}

                log_audit(self.run_id, "tool_result", f"tool:{name}", result)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })

        logger.warning(f"[{self.run_id}] MAX_STEPS reached without final answer.")
        summary["steps_taken"] = steps
        summary["warning"]     = "MAX_STEPS reached"
        summary["comparison_report"] = comparison_report
        return summary

    # ------------------------------------------------------------------
    # Tool schema builder (OpenAI function-calling format)
    # ------------------------------------------------------------------
    def _build_tool_schemas(self) -> list[dict]:
        schemas = {
            "fetch_latest_versions": {
                "type": "function",
                "function": {
                    "name": "fetch_latest_versions",
                    "description": "MCP tool: fetch latest web versions for a category.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "enum": ["SourceOne", "DPS", "Other", "ALL"]}
                        },
                        "required": ["category"],
                    },
                },
            },
            "fetch_current_versions": {
                "type": "function",
                "function": {
                    "name": "fetch_current_versions",
                    "description": "MCP tool: resolve current installed versions using live server then PDF fallback.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "enum": ["SourceOne", "DPS", "Other", "ALL"]}
                        },
                        "required": ["category"],
                    },
                },
            },
            "run_full_pipeline": {
                "type": "function",
                "function": {
                    "name": "run_full_pipeline",
                    "description": "MCP tool: run latest fetch, current fetch, compare, and notification in order.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "enum": ["SourceOne", "DPS", "Other", "ALL"]}
                        },
                        "required": ["category"],
                    },
                },
            },
            "get_software_list": {
                "type": "function",
                "function": {
                    "name": "get_software_list",
                    "description": "Returns software names for a category.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {
                                "type": "string",
                                "enum": ["SourceOne","DPS","Other","ALL"],
                            }
                        },
                        "required": ["category"],
                    },
                },
            },
            "search_latest_version": {
                "type": "function",
                "function": {
                    "name": "search_latest_version",
                    "description": "Web-search latest version for one software.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "software_name": {"type": "string"},
                            "force_refresh": {"type": "boolean", "default": False},
                        },
                        "required": ["software_name"],
                    },
                },
            },
            "query_server": {
                "type": "function",
                "function": {
                    "name": "query_server",
                    "description": "Query live server for installed version.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "software_name": {"type": "string"}
                        },
                        "required": ["software_name"],
                    },
                },
            },
            "extract_from_pdf": {
                "type": "function",
                "function": {
                    "name": "extract_from_pdf",
                    "description": "Extract installed version from PDF (fallback).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "software_name": {"type": "string"}
                        },
                        "required": ["software_name"],
                    },
                },
            },
            "compare_versions": {
                "type": "function",
                "function": {
                    "name": "compare_versions",
                    "description": "Compare latest vs current. Local mode accepts one item; MCP mode accepts no arguments and reads saved JSON files.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "software_name": {"type": "string"},
                            "latest":  {"type": "object"},
                            "current": {"type": "object"},
                        },
                    },
                },
            },
            "send_notification": {
                "type": "function",
                "function": {
                    "name": "send_notification",
                    "description": "Send notification. Local mode accepts report/urgency; MCP mode reads saved comparison report.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "report":  {"type": "object"},
                            "urgency": {
                                "type": "string",
                                "enum": ["OK","WARNING","CRITICAL"],
                            },
                        },
                    },
                },
            },
            "log_audit_event": {
                "type": "function",
                "function": {
                    "name": "log_audit_event",
                    "description": "Log one audit entry for explainability.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "step":    {"type": "string"},
                            "details": {"type": "object"},
                        },
                        "required": ["step"],
                    },
                },
            },
            "get_run_history": {
                "type": "function",
                "function": {
                    "name": "get_run_history",
                    "description": "Get recent run history for drift detection.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "software_name": {"type": "string"},
                            "limit": {"type": "integer", "default": 5},
                        },
                        "required": ["software_name"],
                    },
                },
            },
            "get_recent_failures": {
                "type": "function",
                "function": {
                    "name": "get_recent_failures",
                    "description": "Get recent server/query failures for one software item.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "software_name": {"type": "string"},
                            "limit": {"type": "integer", "default": 3},
                        },
                        "required": ["software_name"],
                    },
                },
            },
            "check_vulnerabilities": {
                "type": "function",
                "function": {
                    "name": "check_vulnerabilities",
                    "description": "Check vulnerabilities for one software/version. Use force_refresh=true to bypass cache.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "software_name": {"type": "string"},
                            "version": {"type": "string"},
                            "needs_update": {"type": "boolean", "default": False},
                            "force_refresh": {"type": "boolean", "default": False},
                        },
                        "required": ["software_name"],
                    },
                },
            },
        }
        return [v for k, v in schemas.items() if k in self.tools]
