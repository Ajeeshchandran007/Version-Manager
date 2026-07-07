# run_now.py
import asyncio
from Utils.utils import load_config
from Core.version_fetcher import VersionFetcher
from Core.server_querier import ServerQuerier
from Core.pdf_reader import PDFReader
from Core.comparator import compare as _compare_fn
from Core.notifier import build_html_report, build_report, send_email
from Utils.software_loader import load_software
from App.server_config import load_server_configs
from agent.agent import VersionManagerAgent
from agent.memory import init_db, log_audit, log_failure, save_run_result, get_run_history, get_recent_failures

async def main():
    config  = load_config()
    fetcher = VersionFetcher()
    querier = ServerQuerier()
    reader  = PDFReader()
    init_db()

    async def get_software_list(category="ALL"):
        return load_software(config["input_files"]["software_yml"], category)

    async def search_latest_version(software_name):
        return await fetcher.fetch(software_name)

    async def query_server(software_name):
        result = await querier.fetch(software_name)
        if result is None:
            server_cfg = load_server_configs(config).get(software_name, {})
            log_failure(
                software_name,
                server_cfg.get("host"),
                "Server query returned no version data",
            )
        return result

    async def extract_from_pdf(software_name):
        return await reader.fetch(software_name)

    async def compare_versions(software_name, latest, current):
        report = _compare_fn({software_name: latest}, {software_name: current})
        return report[software_name]

    async def send_notification(report, urgency="OK"):
        body    = build_report(report)
        html_body = build_html_report(report)
        updates = [n for n, v in report.items() if v.get("needs_update")]
        subject = f"[{urgency}] {len(updates)} update(s) needed" if updates else f"[{urgency}] All up to date"
        sent    = send_email(subject, body, html_body=html_body)
        return {"sent": sent, "subject": subject}

    async def log_audit_event(step, details=None):
        log_audit("agent-tool", step, "agent", details)
        return {"logged": True}

    async def get_run_history_tool(software_name, limit=5):
        return get_run_history(software_name, limit)

    async def get_recent_failures_tool(software_name, limit=3):
        return get_recent_failures(software_name, limit)

    tools = {
        "get_software_list":     get_software_list,
        "search_latest_version": search_latest_version,
        "query_server":          query_server,
        "extract_from_pdf":      extract_from_pdf,
        "compare_versions":      compare_versions,
        "send_notification":     send_notification,
        "log_audit_event":       log_audit_event,
        "get_run_history":       get_run_history_tool,
        "get_recent_failures":   get_recent_failures_tool,
    }

    agent   = VersionManagerAgent(tools)
    summary = await agent.run("Check all software and notify the team.", category="ALL")
    print("\n=== Agent Summary ===")
    print(summary)

asyncio.run(main())
