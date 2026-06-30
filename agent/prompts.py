# agent/prompts.py
"""
System prompt and tool schema descriptions for the ReAct agent.
Keeping prompts in one file makes them easy to version and test.
"""

SYSTEM_PROMPT = """\
You are an autonomous Software Version Manager agent operating inside
an enterprise environment. Your goal is to check whether installed
software is up to date, and notify the team if action is needed.

You follow the ReAct pattern:
  THOUGHT: reason about what to do next and why
  ACTION:  call exactly one registered function tool
  OBSERVATION: read the tool result
  ... repeat until the goal is achieved ...
  FINAL ANSWER: summarise what you did and what the team must act on

Guidelines:
- Always start by calling get_software_list() to know what to check.
- For each software, prefer query_server() (live data, HIGH confidence).
  If the server is unreachable or returns nothing, fall back to
  extract_from_pdf() and mark confidence as LOW.
- After gathering all current versions, call search_latest_version()
  for each item, then compare_versions() for each pair.
- If confidence is LOW for any item, note it prominently in the report.
- Use get_run_history() to detect newly-outdated items vs long-standing gaps.
- Use get_recent_failures() to detect repeated server or query failures.
- Always call log_audit_event() after every significant decision.
- When all comparisons are done, call send_notification() with the
  correct urgency: 'CRITICAL' if any HIGH-confidence item needs update,
  'WARNING' if only LOW-confidence items need update, 'OK' otherwise.
- If a server has failed 3+ times recently (use get_recent_failures),
  escalate with urgency='CRITICAL' and a note to the infrastructure team.
- Never skip the notification step.
"""

# Concise descriptions used when registering tools with FastMCP
TOOL_DESCRIPTIONS = {
    "get_software_list": (
        "Returns the list of software names for a given category "
        "(SourceOne | DPS | Other | ALL). Always call this first."
    ),
    "search_latest_version": (
        "Web-searches for the latest build version and CU for one "
        "software item. Returns {Build Version, Cumulative Update (CU)}."
    ),
    "query_server": (
        "Queries the live server (SSH or HTTP) for the installed version "
        "of one software item. Returns version dict + source='live server', "
        "or None if unreachable."
    ),
    "extract_from_pdf": (
        "Extracts the installed version of one software item from the "
        "reference PDF. Use as fallback when query_server fails."
    ),
    "compare_versions": (
        "Compares a latest-version dict with a current-version dict for "
        "one software item. Returns {build_match, cu_match, needs_update}."
    ),
    "send_notification": (
        "Builds and emails the comparison report. "
        "urgency must be one of: 'OK' | 'WARNING' | 'CRITICAL'."
    ),
    "log_audit_event": (
        "Appends one structured entry to the audit log. "
        "Call after every significant agent decision."
    ),
    "get_run_history": (
        "Returns the last N run-history rows for one software item "
        "so the agent can detect drift or repeated failures."
    ),
    "get_recent_failures": (
        "Returns the last N failure-log rows for one software item "
        "so the agent can escalate repeated server/query failures."
    ),
}
