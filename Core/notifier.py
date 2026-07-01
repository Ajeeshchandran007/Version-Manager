"""Builds a concise assessment report and sends it via SMTP."""
from __future__ import annotations

import smtplib
from pathlib import Path
from html import escape
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from Core.policy import PolicyError, require_approval
from Utils.utils import logger, load_config

LAST_EMAIL_ERROR: str | None = None


def build_report(
    comparison: dict[str, dict],
    vulnerabilities: dict[str, dict] | None = None,
) -> str:
    vulnerabilities = vulnerabilities or {}
    stats = _summary_stats(comparison, vulnerabilities)
    priority_rows = _priority_rows(comparison, vulnerabilities)

    lines = [
        "Software Version & Security Assessment Report",
        "=" * 52,
        "",
        "EXECUTIVE SUMMARY",
        "-" * 52,
        f"Total Applications Scanned : {stats['total']}",
        f"Applications Requiring Update : {stats['needs_update']}",
        f"Critical Risk Items       : {stats['critical']}",
        f"High Risk Items           : {stats['high']}",
        f"Medium Risk Items         : {stats['medium']}",
        f"Low Risk Items            : {stats['low']}",
        f"Overall Security Posture  : {stats['posture']}",
        "",
    ]

    if priority_rows:
        lines.extend([
            "PRIORITY UPDATE REQUIRED",
            "-" * 52,
            _format_table(
                ["Software", "Current Version", "Latest Version", "Gap", "Security Risk", "Update Priority"],
                priority_rows,
            ),
            "",
        ])
    else:
        lines.extend(["PRIORITY UPDATE REQUIRED", "-" * 52, "No updates required.", ""])

    lines.extend([
        "SECURITY RISK SUMMARY",
        "-" * 52,
        _security_summary(vulnerabilities),
        "",
    ])
    security_rows = _security_risk_rows(vulnerabilities)
    if security_rows:
        lines.extend([
            "SECURITY RISK CONTRIBUTORS",
            "-" * 52,
            _format_table(["Software", "Risk Level", "Basis", "Reason"], security_rows),
            "",
        ])

    lines.extend([
        "DETAILED OBSERVATIONS",
        "-" * 52,
    ])
    lines.extend(_observations(comparison, vulnerabilities))

    lines.extend([
        "",
        "RISK CLASSIFICATION",
        "-" * 52,
        _format_table(
            ["Level", "Meaning"],
            [
                ["Critical", "Active exploit or known severe CVE"],
                ["High", "Unsupported or severely outdated"],
                ["Medium", "Outdated with potential exposure or failed CVE lookup"],
                ["Low", "No known active CVEs detected; version drift may remain"],
            ],
        ),
        "",
        f"Current Overall Rating: {stats['overall_rating']}",
    ])

    unknown = [n for n, v in comparison.items() if v.get("unknown")]
    if unknown:
        lines.extend(["", "UNKNOWN VERSION STATUS", "-" * 52])
        for name in unknown:
            item = comparison[name]
            lines.append(
                f"{name}: source={item.get('current_source', 'unknown')}, "
                f"current={_format_version(item.get('current') or {})}, "
                f"latest={_format_version(item.get('latest') or {})}"
            )

    return "\n".join(lines)


def build_security_report(vulnerabilities: dict[str, dict]) -> str:
    """Compatibility helper used by older callers."""
    return _security_summary(vulnerabilities)


def count_actionable_updates(comparison: dict[str, dict], vulnerabilities: dict[str, dict] | None = None) -> int:
    """Return the number of update rows that will be shown in the notification."""
    return len(_priority_rows(comparison, vulnerabilities or {}))


def is_actionable_update(item: dict[str, Any]) -> bool:
    """Return whether a comparison item represents a real remediation candidate."""
    return bool(item.get("needs_update")) and _version_gap(item) != "No version gap"


def build_html_report(
    comparison: dict[str, dict],
    vulnerabilities: dict[str, dict] | None = None,
) -> str:
    """Build an Outlook-friendly styled HTML assessment report."""
    vulnerabilities = vulnerabilities or {}
    stats = _summary_stats(comparison, vulnerabilities)
    priority_rows = _priority_rows(comparison, vulnerabilities)
    observations = _observations(comparison, vulnerabilities)
    total_cves = sum(len(v.get("cves") or []) for v in vulnerabilities.values())
    highest = _highest_severity(vulnerabilities)
    overall_risk = _highest_risk(vulnerabilities)

    return f"""<!doctype html>
<html>
<head>
  <meta http-equiv="Content-Type" content="text/html; charset=utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background-color:#f4f6f8;font-family:Segoe UI,Arial,sans-serif;color:#17202a;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background-color:#f4f6f8;border-collapse:collapse;">
    <tr>
      <td align="center" style="padding:24px 12px;">
        <table role="presentation" width="1100" cellspacing="0" cellpadding="0" border="0" style="width:1100px;max-width:1100px;background-color:#ffffff;border-collapse:collapse;border:1px solid #d8dee8;">
          <tr>
            <td style="background-color:#0f172a;color:#ffffff;padding:22px 26px;">
              <div style="font-size:22px;line-height:28px;font-weight:700;">Software Version &amp; Security Assessment Report</div>
              <div style="font-size:13px;line-height:18px;color:#cbd5e1;padding-top:6px;">Automated version drift and vulnerability assessment</div>
            </td>
          </tr>
          <tr>
            <td style="padding:22px 26px;background-color:#ffffff;">
              {_section_header("Executive Summary")}
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="border-collapse:collapse;margin:0 0 18px 0;">
                <tr>
                  {_metric_card("Applications Scanned", stats["total"], "#2563eb")}
                  {_metric_card("Need Update", stats["needs_update"], "#f97316")}
                  {_metric_card("Critical", stats["critical"], "#dc2626")}
                </tr>
                <tr>
                  {_metric_card("High", stats["high"], "#ea580c")}
                  {_metric_card("Medium", stats["medium"], "#ca8a04")}
                  {_metric_card("Low", stats["low"], "#16a34a")}
                </tr>
              </table>
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="border-collapse:collapse;margin:0 0 18px 0;">
                <tr>
                  <td style="padding:12px 14px;background-color:{_risk_bg(stats['overall_rating'])};border-left:5px solid {_risk_color(stats['overall_rating'])};font-size:13px;line-height:19px;">
                    <strong>Overall Security Posture:</strong> {_badge(stats["overall_rating"])} {escape(stats["posture"])}
                  </td>
                </tr>
              </table>

              {_section_header("Priority Update Required")}
              {_html_table(["Software", "Current Version", "Latest Version", "Gap", "Security Risk", "Update Priority"], priority_rows, risk_cols={4, 5}, empty_message="No updates required.")}

              {_section_header("Security Risk Summary")}
              {_html_table(
          ["Scope", "CVE Count", "Highest Severity", "Risk Level", "Status"],
          [[
              "All Applications",
              str(total_cves),
              highest,
              overall_risk,
              "No known active CVEs detected by current NVD keyword scan" if total_cves == 0 else f"{total_cves} CVE record(s) detected; review affected applications",
          ]],
          risk_col=3,
      )}
              {_html_security_risk_contributors(vulnerabilities)}

              {_section_header("Detailed Observations")}
              <ul style="margin:8px 0 18px 20px;padding:0;font-size:13px;line-height:20px;">
                {''.join(f'<li>{escape(line.lstrip("- "))}</li>' for line in observations)}
              </ul>

              {_section_header("Risk Classification")}
              {_html_table(
          ["Level", "Meaning"],
          [
              ["Critical", "Active exploit or known severe CVE"],
              ["High", "Unsupported or severely outdated"],
              ["Medium", "Outdated with potential exposure or failed CVE lookup"],
              ["Low", "No known active CVEs detected; version drift may remain"],
          ],
          risk_col=0,
      )}
            </td>
          </tr>
          <tr>
            <td style="background-color:#e8edf5;color:#475569;font-size:12px;line-height:18px;padding:12px 26px;border-top:1px solid #d8dee8;">
              Generated by Version Manager. Validate upgrade plans with application owners before production changes.
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def send_email(
    subject: str,
    body: str,
    html_body: str | None = None,
    attachments: list[str | Path] | None = None,
) -> bool:
    global LAST_EMAIL_ERROR
    LAST_EMAIL_ERROR = None

    config = load_config()
    smtp_cfg = config["smtp"]

    try:
        require_approval("send_email", risk="medium")
    except PolicyError as exc:
        LAST_EMAIL_ERROR = str(exc)
        logger.warning(f"Email blocked by policy: {LAST_EMAIL_ERROR}")
        return False

    msg = MIMEMultipart("mixed")
    msg["From"] = smtp_cfg["sender"]
    msg["To"] = ", ".join(smtp_cfg["recipients"])
    msg["Subject"] = subject

    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(body, "plain"))
    if html_body:
        alternative.attach(MIMEText(html_body, "html"))
    msg.attach(alternative)

    for attachment in attachments or []:
        path = Path(attachment)
        if not path.exists() or not path.is_file():
            logger.warning(f"Email attachment not found: {path}")
            continue
        part = MIMEApplication(path.read_bytes(), Name=path.name)
        part["Content-Disposition"] = f'attachment; filename="{path.name}"'
        msg.attach(part)

    try:
        with smtplib.SMTP(smtp_cfg["server"], smtp_cfg["port"]) as srv:
            srv.starttls()
            srv.login(smtp_cfg["user"], smtp_cfg["password"])
            srv.sendmail(smtp_cfg["sender"], smtp_cfg["recipients"], msg.as_string())
        logger.info(f"Email sent to {smtp_cfg['recipients']}")
        return True
    except Exception as e:
        LAST_EMAIL_ERROR = str(e)
        logger.error(f"Email send failed: {LAST_EMAIL_ERROR}")
        return False


def get_last_email_error() -> str | None:
    return LAST_EMAIL_ERROR


def _summary_stats(
    comparison: dict[str, dict],
    vulnerabilities: dict[str, dict],
) -> dict[str, Any]:
    total = len(comparison)
    actionable = [name for name, item in comparison.items() if is_actionable_update(item)]
    needs_update = len(actionable)
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for name in actionable:
        risk = _risk(name, vulnerabilities)
        counts[risk.lower()] = counts.get(risk.lower(), 0) + 1

    if counts["critical"]:
        overall = "CRITICAL"
    elif counts["high"]:
        overall = "HIGH"
    elif needs_update:
        overall = "MEDIUM"
    else:
        overall = "LOW"

    posture = (
        "Requires Maintenance (outdated software versions detected)"
        if needs_update else "Healthy (all scanned versions are current)"
    )
    return {
        "total": total,
        "needs_update": needs_update,
        **counts,
        "overall_rating": overall,
        "posture": posture,
    }


def _priority_rows(comparison: dict[str, dict], vulnerabilities: dict[str, dict]) -> list[list[str]]:
    rows = []
    for software, item in comparison.items():
        if not is_actionable_update(item):
            continue
        security_risk = _risk(software, vulnerabilities)
        update_priority = _business_risk(software, item, vulnerabilities)
        rows.append([
            _display_name(software),
            _format_version(item.get("current") or {}),
            _format_version(item.get("latest") or {}),
            _version_gap(item),
            security_risk,
            update_priority,
        ])
    return sorted(rows, key=lambda row: (_risk_rank(row[5]), _risk_rank(row[4]), row[0]))


def _security_summary(vulnerabilities: dict[str, dict]) -> str:
    if not vulnerabilities:
        return "No vulnerability data available."

    total_cves = sum(len(v.get("cves") or []) for v in vulnerabilities.values())
    highest = _highest_severity(vulnerabilities)
    overall_risk = _highest_risk(vulnerabilities)

    if total_cves == 0:
        status = "No known active CVEs detected by current NVD keyword scan"
    else:
        status = f"{total_cves} CVE record(s) detected; review affected applications"

    return _format_table(
        ["Scope", "CVE Count", "Highest Severity", "Risk Level", "Status"],
        [["All Applications", str(total_cves), highest, overall_risk, status]],
    )


def _security_risk_rows(vulnerabilities: dict[str, dict]) -> list[list[str]]:
    rows = []
    for software, record in vulnerabilities.items():
        risk = _risk(software, vulnerabilities)
        if risk not in {"Critical", "High", "Medium"}:
            continue
        policy_findings = record.get("policy_findings") or []
        cves = record.get("cves") or []
        if policy_findings:
            basis = str(policy_findings[0].get("severity") or record.get("severity") or "Policy")
            reason = str(policy_findings[0].get("reason") or record.get("assessment") or "")
        elif cves:
            basis = f"{len(cves)} CVE(s)"
            reason = str(record.get("assessment") or "")
        else:
            basis = str(record.get("severity") or "Assessment")
            reason = str(record.get("assessment") or "")
        rows.append([_display_name(software), risk, basis, _short_text(reason, 120)])
    return sorted(rows, key=lambda row: (_risk_rank(row[1]), row[0]))


def _html_security_risk_contributors(vulnerabilities: dict[str, dict]) -> str:
    rows = _security_risk_rows(vulnerabilities)
    if not rows:
        return ""
    return (
        f'<div style="font-size:14px;line-height:20px;font-weight:700;margin:16px 0 6px 0;color:#0f172a;">Security Risk Contributors</div>'
        f'{_html_table(["Software", "Risk Level", "Basis", "Reason"], rows, risk_col=1)}'
    )


def _observations(
    comparison: dict[str, dict],
    vulnerabilities: dict[str, dict],
) -> list[str]:
    priority_rows = _priority_rows(comparison, vulnerabilities)
    cve_count = sum(len(v.get("cves") or []) for v in vulnerabilities.values())
    failed_lookups = [name for name, v in vulnerabilities.items() if v.get("source") == "local-assessment-nvd-error"]
    priority = [row[0] for row in priority_rows[:4]]

    lines = [
        f"- {len(priority_rows)} application(s) are behind latest vendor releases.",
        f"- {cve_count} active CVE record(s) were detected in this scan.",
        "- Risk is primarily version drift and potential future exposure."
        if cve_count == 0 else
        "- Risk includes detected CVEs and version drift.",
    ]
    if failed_lookups:
        lines.append(f"- CVE lookup did not complete for: {', '.join(failed_lookups)}.")
    if priority:
        lines.append(f"- Priority remediation focus: {', '.join(priority)}.")
    return lines


def _format_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [
        max(len(str(header)), *(len(str(row[i])) for row in rows))
        for i, header in enumerate(headers)
    ]
    header_line = " | ".join(str(header).ljust(widths[i]) for i, header in enumerate(headers))
    separator = "-+-".join("-" * width for width in widths)
    body = [
        " | ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row))
        for row in rows
    ]
    return "\n".join([header_line, separator, *body])


def _html_table(
    headers: list[str],
    rows: list[list[str]],
    risk_col: int | None = None,
    risk_cols: set[int] | None = None,
    empty_message: str | None = None,
) -> str:
    badge_cols = risk_cols if risk_cols is not None else ({risk_col} if risk_col is not None else set())
    header = "".join(
        f'<th align="left" style="text-align:left;background-color:#eef2f7;color:#334155;border:1px solid #d7dde8;padding:9px;font-size:12px;line-height:16px;font-weight:700;">{escape(h)}</th>'
        for h in headers
    )
    body_rows = []
    for row in rows:
        cells = []
        for idx, cell in enumerate(row):
            value = str(cell)
            rendered = _badge(value) if idx in badge_cols and value in {"Critical", "High", "Medium", "Low"} else escape(value)
            cells.append(f'<td style="border:1px solid #e2e8f0;padding:9px;font-size:12px;line-height:16px;vertical-align:top;">{rendered}</td>')
        body_rows.append(f"<tr>{''.join(cells)}</tr>")
    if not body_rows and empty_message:
        body_rows.append(
            f'<tr><td colspan="{len(headers)}" style="border:1px solid #e2e8f0;padding:10px;font-size:12px;line-height:16px;color:#475569;">{escape(empty_message)}</td></tr>'
        )
    return f'<table width="100%" cellspacing="0" cellpadding="0" border="0" style="border-collapse:collapse;margin:10px 0 18px 0;background-color:#ffffff;"><tr>{header}</tr>{"".join(body_rows)}</table>'


def _metric_card(label: str, value: Any, color: str) -> str:
    return f"""
    <td width="33.33%" style="padding:0 8px 8px 0;vertical-align:top;">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="border-collapse:collapse;border:1px solid #e2e8f0;background-color:#ffffff;">
        <tr>
          <td width="5" style="width:5px;background-color:{color};font-size:0;line-height:0;">&nbsp;</td>
          <td style="padding:12px;">
            <div style="font-size:11px;line-height:15px;color:#64748b;text-transform:uppercase;">{escape(label)}</div>
            <div style="font-size:22px;line-height:28px;font-weight:700;color:#0f172a;">{escape(str(value))}</div>
          </td>
        </tr>
      </table>
    </td>"""


def _badge(risk: str) -> str:
    color = _risk_color(risk)
    bg = _risk_bg(risk)
    return f'<span style="display:inline-block;padding:3px 8px;background-color:{bg};color:{color};font-weight:700;font-size:12px;line-height:16px;">{escape(risk)}</span>'


def _risk_color(risk: str) -> str:
    return {
        "Critical": "#b91c1c",
        "High": "#c2410c",
        "Medium": "#a16207",
        "Low": "#15803d",
    }.get(risk, "#475569")


def _risk_bg(risk: str) -> str:
    return {
        "Critical": "#fee2e2",
        "High": "#ffedd5",
        "Medium": "#fef3c7",
        "Low": "#dcfce7",
    }.get(risk, "#f1f5f9")


def _risk_css(risk: str) -> str:
    return f"color:{_risk_color(risk)};background:{_risk_bg(risk)};"


def _section_header(title: str) -> str:
    return (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" '
        'style="border-collapse:collapse;margin:24px 0 8px 0;">'
        f'<tr><td style="font-size:16px;line-height:22px;font-weight:700;color:#0f172a;'
        f'border-bottom:2px solid #e2e8f0;padding-bottom:6px;">{escape(title)}</td></tr></table>'
    )




def _format_version(version_info: dict[str, Any]) -> str:
    build = version_info.get("Build Version") or "Unknown"
    cu = version_info.get("Cumulative Update (CU)")
    return f"{build} ({cu})" if cu else build


def _version_gap(item: dict[str, Any]) -> str:
    current = item.get("current") or {}
    latest = item.get("latest") or {}
    current_build = str(current.get("Build Version") or "").strip()
    latest_build = str(latest.get("Build Version") or "").strip()
    current_cu = current.get("Cumulative Update (CU)")
    latest_cu = latest.get("Cumulative Update (CU)")
    builds_match = bool(current_build and latest_build and current_build.lower() == latest_build.lower())
    cus_match = str(current_cu or "").strip().lower() == str(latest_cu or "").strip().lower()
    if builds_match and cus_match:
        return "No version gap"

    if builds_match and current_cu and latest_cu:
        diff = _cu_number(latest_cu) - _cu_number(current_cu)
        if diff > 0:
            return f"{diff} CU(s) behind"
        return "CU mismatch"

    current_major = _major_version(current_build)
    latest_major = _major_version(latest_build)
    if current_major is not None and latest_major is not None:
        if latest_major - current_major >= 2:
            return "Major version gap"
        if latest_major > current_major:
            return "Major upgrade"
        if latest_major == current_major:
            return "Patch/build gap"
    return "Build/version gap"


def _risk(software: str, vulnerabilities: dict[str, dict]) -> str:
    risk = (vulnerabilities.get(software) or {}).get("risk_level", "LOW")
    return {
        "CRITICAL": "Critical",
        "HIGH": "High",
        "MEDIUM": "Medium",
        "LOW": "Low",
    }.get(str(risk).upper(), "Low")


def _business_risk(software: str, item: dict[str, Any], vulnerabilities: dict[str, dict]) -> str:
    security_risk = _risk(software, vulnerabilities)
    if security_risk in {"Critical", "High"}:
        return security_risk

    gap = _version_gap(item).lower()
    name = software.lower()
    enterprise_impact = any(token in name for token in ["exchange", "sql server", "openssl", "elastic"])
    if "cu(s) behind" in gap or "major" in gap or enterprise_impact:
        return "Medium"
    return security_risk


def _highest_risk(vulnerabilities: dict[str, dict]) -> str:
    risks = [_risk(name, vulnerabilities) for name in vulnerabilities]
    return min(risks or ["Low"], key=_risk_rank)


def _highest_severity(vulnerabilities: dict[str, dict]) -> str:
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "NONE": 4, "UNKNOWN": 5, "POTENTIAL": 2}
    severities = [str(v.get("severity", "NONE")).upper() for v in vulnerabilities.values()]
    return min(severities or ["NONE"], key=lambda value: order.get(value, 5))


def _risk_rank(risk: str) -> int:
    return {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}.get(risk, 4)


def _short_text(text: str, limit: int) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def _cu_number(cu: str) -> int:
    digits = "".join(ch for ch in cu if ch.isdigit())
    return int(digits or 0)


def _major_version(version: str | None) -> int | None:
    if not version:
        return None
    digits = version.split(".", 1)[0]
    return int(digits) if digits.isdigit() else None


def _display_name(software: str) -> str:
    return "Microsoft Edge" if software.lower() == "edge" else software


def _owner(software: str) -> str:
    name = software.lower()
    if "exchange" in name:
        return "Messaging Team"
    if "sql" in name:
        return "DBA Team"
    if "openssl" in name or "curl" in name:
        return "Security Team"
    if "elastic" in name:
        return "Platform Team"
    if "edge" in name or "outlook" in name:
        return "Endpoint Team"
    return "Application Owner"


def _action_priority(risk: str) -> str:
    if risk in {"Critical", "High"}:
        return "High"
    if risk == "Medium":
        return "Medium"
    return "Low"
