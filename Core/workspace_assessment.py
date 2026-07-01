"""Release engineering and QA workspace assessment logic."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any

from Core.notifier import is_actionable_update


def value(record: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return default


def vendor_for(name: str) -> str:
    lookup = {
        "sql": "Microsoft",
        "exchange": "Microsoft",
        "outlook": "Microsoft",
        "edge": "Microsoft",
        "openssl": "OpenSSL",
        "libcurl": "curl",
        "hcl": "HCL",
        "elastic": "Elastic",
        "adobe": "Adobe",
        "snagit": "TechSmith",
    }
    lowered = name.lower()
    for key, vendor in lookup.items():
        if key in lowered:
            return vendor
    return "Unknown"


def version_gap(current_version: str, latest_version: str, current_cu: str = "", latest_cu: str = "") -> str:
    current_version = str(current_version or "").strip()
    latest_version = str(latest_version or "").strip()
    current_cu = str(current_cu or "").strip()
    latest_cu = str(latest_cu or "").strip()
    if not current_version or not latest_version:
        return "Unknown"
    if current_version.lower() == latest_version.lower() and current_cu.lower() == latest_cu.lower():
        return "None"
    if current_version.lower() == latest_version.lower() and current_cu and latest_cu and current_cu.lower() != latest_cu.lower():
        return "CU Gap"
    if mixed_version_scheme(current_version, latest_version):
        return "Source Review"
    current_major = current_version.split(".")[0]
    latest_major = latest_version.split(".")[0]
    if current_major and latest_major and current_major != latest_major:
        return "Major Gap"
    return "Patch Gap"


def version_order(current_version: str, latest_version: str) -> str:
    current_parts = _numeric_version_parts(current_version)
    latest_parts = _numeric_version_parts(latest_version)
    if not current_parts or not latest_parts:
        return "unknown"
    max_len = max(len(current_parts), len(latest_parts))
    current_parts.extend([0] * (max_len - len(current_parts)))
    latest_parts.extend([0] * (max_len - len(latest_parts)))
    if latest_parts > current_parts:
        return "target_newer"
    if latest_parts < current_parts:
        return "target_older"
    return "same"


def _numeric_version_parts(version: str) -> list[int]:
    text = str(version or "").strip()
    if not text or text.lower() in {"unknown", "none", "null"}:
        return []
    parts = re.findall(r"\d+", text)
    return [int(part) for part in parts[:4]]


def mixed_version_scheme(current_version: str, latest_version: str) -> bool:
    current_parts = _numeric_version_parts(current_version)
    latest_parts = _numeric_version_parts(latest_version)
    if not current_parts or not latest_parts:
        return False
    if current_parts[0] == latest_parts[0]:
        return False
    return max(current_parts[0], latest_parts[0]) >= 100 or abs(current_parts[0] - latest_parts[0]) >= 50


def blocker_reason(
    name: str,
    current_version: str,
    latest_version: str,
    current_cu: str,
    latest_cu: str,
    gap: str,
    risk: str,
    has_target: bool,
    needs_update: bool,
) -> tuple[str, str]:
    if not has_target:
        return "Blocked", "Latest vendor version is unknown; validate vendor catalog before packaging."

    order = version_order(current_version, latest_version)
    if order == "target_older":
        return (
            "Blocked",
            f"Target version {latest_version} appears lower than installed version {current_version}; verify latest-version source before packaging.",
        )

    if not needs_update:
        return "Ready for Packaging", ""

    if gap == "Source Review":
        return (
            "Dependency Review Required",
            f"Current version {current_version} and target version {latest_version} appear to use different version schemes; validate vendor source mapping before packaging.",
        )

    if current_cu and latest_cu and current_cu != latest_cu:
        return (
            "Dependency Review Required",
            f"Cumulative update gap detected ({current_cu} to {latest_cu}); validate prerequisites, backup, and rollback plan.",
        )

    lowered = name.lower()
    if risk in {"CRITICAL", "HIGH"}:
        return (
            "Dependency Review Required",
            "High security or enterprise-impact risk; prioritize owner review and controlled package validation.",
        )

    if gap == "Major Gap":
        if any(token in lowered for token in ["sql", "exchange", "hcl", "elastic"]):
            return (
                "Dependency Review Required",
                "Major enterprise application upgrade; validate OS/runtime/database prerequisites and upgrade sequence.",
            )
        return (
            "Dependency Review Required",
            "Major version upgrade; validate compatibility, silent install behavior, and rollback before packaging.",
        )

    if gap == "CU Gap":
        return (
            "Dependency Review Required",
            "Cumulative update gap detected; validate service pack/CU prerequisites and maintenance window.",
        )

    return "Vendor Patch Available", ""


def readiness_owner(name: str) -> str:
    lowered = name.lower()
    if "exchange" in lowered:
        return "Messaging Team"
    if "sql" in lowered:
        return "DBA Team"
    if "openssl" in lowered or "curl" in lowered:
        return "Security Team"
    if "elastic" in lowered:
        return "Platform Team"
    if "adobe" in lowered or "edge" in lowered or "outlook" in lowered:
        return "Endpoint Team"
    return "Application Owner"


def installer_type(name: str) -> str:
    lowered = name.lower()
    if any(token in lowered for token in ["sql", "exchange", "hcl", "elastic"]):
        return "Enterprise Installer"
    if any(token in lowered for token in ["adobe", "snagit", "edge", "outlook"]):
        return "MSI/EXE"
    if any(token in lowered for token in ["openssl", "curl"]):
        return "Runtime Package"
    return "Vendor Package"


def vendor_resource_links(name: str) -> dict[str, str]:
    lowered = name.lower()
    if "sql" in lowered:
        return {
            "Release Notes": "https://learn.microsoft.com/en-us/troubleshoot/sql/releases/sqlserver-2019/build-versions",
            "Download Link": "https://www.microsoft.com/download/details.aspx?id=100809",
        }
    if "exchange" in lowered:
        return {
            "Release Notes": "https://learn.microsoft.com/en-us/exchange/new-features/build-numbers-and-release-dates",
            "Download Link": "https://www.microsoft.com/download/details.aspx?id=105878",
        }
    if "edge" in lowered:
        return {
            "Release Notes": "https://learn.microsoft.com/en-us/deployedge/microsoft-edge-relnote-stable-channel",
            "Download Link": "https://www.microsoft.com/edge/business/download",
        }
    if "outlook" in lowered or "office" in lowered:
        return {
            "Release Notes": "https://learn.microsoft.com/en-us/officeupdates/update-history-office-2019",
            "Download Link": "https://learn.microsoft.com/en-us/deployoffice/office2019/deploy",
        }
    if "openssl" in lowered:
        return {
            "Release Notes": "https://openssl-library.org/news/openssl-3.5-notes/",
            "Download Link": "https://openssl-library.org/source/",
        }
    if "libcurl" in lowered or "curl" in lowered:
        return {
            "Release Notes": "https://curl.se/changes.html",
            "Download Link": "https://curl.se/download.html",
        }
    if "elastic" in lowered or "elasticsearch" in lowered:
        return {
            "Release Notes": "https://www.elastic.co/guide/en/elasticsearch/reference/current/es-release-notes.html",
            "Download Link": "https://www.elastic.co/downloads/elasticsearch",
        }
    if "hcl domino" in lowered:
        return {
            "Release Notes": "https://support.hcl-software.com/csm?id=kb_article&sysparm_article=KB0100008",
            "Download Link": "https://support.hcl-software.com/csm",
        }
    if "hcl notes" in lowered:
        return {
            "Release Notes": "https://support.hcl-software.com/csm?id=kb_article&sysparm_article=KB0100009",
            "Download Link": "https://support.hcl-software.com/csm",
        }
    if "adobe" in lowered or "acrobat" in lowered:
        return {
            "Release Notes": "https://helpx.adobe.com/creative-cloud/release-note/cc-release-notes.html",
            "Download Link": "https://helpx.adobe.com/download-install.html",
        }
    if "snagit" in lowered:
        return {
            "Release Notes": "https://support.techsmith.com/hc/en-us/sections/360008596752-Snagit-Release-Notes",
            "Download Link": "https://www.techsmith.com/download/snagit/",
        }
    if "7-zip" in lowered or "7zip" in lowered:
        return {
            "Release Notes": "https://www.7-zip.org/history.txt",
            "Download Link": "https://www.7-zip.org/download.html",
        }
    if "filezilla" in lowered:
        return {
            "Release Notes": "https://filezilla-project.org/versions.php",
            "Download Link": "https://filezilla-project.org/download.php",
        }
    if "vlc" in lowered:
        return {
            "Release Notes": "https://www.videolan.org/vlc/releases/",
            "Download Link": "https://www.videolan.org/vlc/",
        }
    if "gimp" in lowered:
        return {
            "Release Notes": "https://www.gimp.org/release-notes/",
            "Download Link": "https://www.gimp.org/downloads/",
        }
    if "windirstat" in lowered:
        return {
            "Release Notes": "https://github.com/windirstat/windirstat/releases",
            "Download Link": "https://windirstat.net/",
        }
    if "winmerge" in lowered:
        return {
            "Release Notes": "https://winmerge.org/downloads/",
            "Download Link": "https://winmerge.org/downloads/",
        }
    if "putty" in lowered:
        return {
            "Release Notes": "https://www.chiark.greenend.org.uk/~sgtatham/putty/changes.html",
            "Download Link": "https://www.chiark.greenend.org.uk/~sgtatham/putty/latest.html",
        }
    if "wireshark" in lowered:
        return {
            "Release Notes": "https://www.wireshark.org/docs/relnotes/",
            "Download Link": "https://www.wireshark.org/download.html",
        }
    if "notepad++" in lowered or "notepad" in lowered:
        return {
            "Release Notes": "https://notepad-plus-plus.org/downloads/",
            "Download Link": "https://notepad-plus-plus.org/downloads/",
        }
    if "apache struts" in lowered:
        return {
            "Release Notes": "https://struts.apache.org/releases.html",
            "Download Link": "https://struts.apache.org/download.cgi",
        }
    if "davinci resolve" in lowered:
        return {
            "Release Notes": "https://www.blackmagicdesign.com/support/family/davinci-resolve-and-fusion",
            "Download Link": "https://www.blackmagicdesign.com/products/davinciresolve",
        }
    if "freeplane" in lowered or "freemind" in lowered:
        return {
            "Release Notes": "https://github.com/freeplane/freeplane/releases",
            "Download Link": "https://sourceforge.net/projects/freeplane/files/freeplane%20stable/",
        }
    if "corel" in lowered:
        return {
            "Release Notes": "https://www.coreldraw.com/en/support/updates/",
            "Download Link": "https://www.coreldraw.com/en/pages/download/",
        }
    if "inkscape" in lowered:
        return {
            "Release Notes": "https://inkscape.org/release/",
            "Download Link": "https://inkscape.org/release/",
        }
    if "pdf24" in lowered:
        return {
            "Release Notes": "https://tools.pdf24.org/en/changelog",
            "Download Link": "https://tools.pdf24.org/en/creator",
        }
    if "mindjet" in lowered or "mindmanager" in lowered:
        return {
            "Release Notes": "https://www.mindmanager.com/en/support/product-resources/release-notes/",
            "Download Link": "https://www.mindmanager.com/en/support/download-library/",
        }
    if "xmlspy" in lowered or "xmlpad" in lowered or "altova" in lowered:
        return {
            "Release Notes": "https://www.altova.com/whatsnew",
            "Download Link": "https://www.altova.com/download",
        }
    return {
        "Release Notes": "Vendor release-note URL not captured. Add this product to vendor_resource_links().",
        "Download Link": "Vendor download URL not captured. Use the approved enterprise software repository.",
    }


def compatibility_requirements(name: str, target_version: str = "") -> dict[str, str]:
    lowered = name.lower()
    links = vendor_resource_links(name)
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    has_vendor_link = not str(links["Release Notes"]).startswith("Vendor release-note URL not captured")

    def requirement_record(
        *,
        supported_os: str = "Not available",
        supported_runtime: str = "Not available",
        supported_browser: str = "Not available",
        supported_database: str = "Not available",
        supported_architecture: str = "Not available",
        source: str = "Not available",
        confidence: str = "Not available",
    ) -> dict[str, str]:
        return {
            "Supported OS": supported_os,
            "Supported Runtime": supported_runtime,
            "Supported Browser": supported_browser,
            "Database Dependency": supported_database,
            "Supported Architecture": supported_architecture,
            "Requirement Source": source,
            "Requirement Source URL": links["Release Notes"],
            "Requirement Confidence": confidence,
            "Last Verified": now,
        }

    endpoint_default = requirement_record(
        source="Vendor release notes mapped; compatibility requirements not extracted" if has_vendor_link else "Vendor source not mapped",
        confidence="Low" if has_vendor_link else "Not available",
    )
    if "exchange" in lowered:
        return requirement_record(
            supported_os="Windows Server versions supported by the target Exchange CU",
            supported_runtime="Vendor-supported .NET build for the target Exchange CU",
            supported_browser="Microsoft Edge / Chrome for EAC and OWA validation",
            supported_database="Exchange mailbox database and AD schema compatibility",
            supported_architecture="x64",
            source="Microsoft Exchange vendor documentation",
            confidence="Medium",
        )
    if "sql" in lowered:
        return requirement_record(
            supported_os="Windows Server versions supported by target SQL Server build",
            supported_runtime=".NET Framework 4.7.2+ recommended 4.8; Visual C++ Redistributable 2015-2022; SQL Server connectivity drivers as required",
            supported_browser="Not applicable",
            supported_database="SQL Server database engine compatibility",
            supported_architecture="x64",
            source="Microsoft SQL Server vendor documentation",
            confidence="Medium",
        )
    if "edge" in lowered:
        return requirement_record(
            supported_os="Windows 10/11 and supported Windows Server builds",
            supported_runtime="Not applicable",
            supported_browser="Target browser",
            supported_database="Not applicable",
            supported_architecture="x64",
            source="Microsoft Edge enterprise release documentation",
            confidence="Medium",
        )
    if "outlook" in lowered:
        return requirement_record(
            supported_os="Windows versions supported by the target Office/Outlook release",
            supported_runtime="Microsoft Office runtime prerequisites",
            supported_browser="Microsoft Edge / Chrome for sign-in and add-ins",
            supported_database="Exchange / Microsoft 365 mailbox compatibility",
            supported_architecture="x64",
            source="Microsoft Office/Outlook vendor documentation",
            confidence="Medium",
        )
    if "hcl domino" in lowered:
        return requirement_record(
            supported_os="Windows Server / Linux versions supported by target Domino release",
            supported_runtime="Not applicable",
            supported_browser="Supported enterprise browser for web/admin clients",
            supported_database="Domino NSF/application template compatibility",
            supported_architecture="x64",
            source="HCL Domino vendor documentation",
            confidence="Medium",
        )
    if "hcl notes" in lowered:
        return requirement_record(
            supported_os="Windows versions supported by target HCL Notes release",
            supported_runtime="Not available",
            supported_browser="Supported browser for embedded/web components",
            supported_database="Domino server compatibility",
            supported_architecture="x64",
            source="HCL Notes vendor documentation",
            confidence="Medium",
        )
    if "adobe" in lowered or "acrobat" in lowered:
        return requirement_record(
            supported_os="Windows versions supported by target Adobe desktop release",
            supported_runtime="Creative Cloud / Adobe desktop prerequisites",
            supported_browser="Microsoft Edge / Chrome for sign-in services",
            supported_database="Not applicable",
            supported_architecture="x64",
            source="Adobe release notes / system requirements",
            confidence="Medium",
        )
    if "openssl" in lowered or "curl" in lowered:
        return requirement_record(
            supported_os="Platform support depends on packaged distribution",
            supported_runtime="Native runtime package",
            supported_browser="Not applicable",
            supported_database="Not applicable",
            supported_architecture="x64",
            source="Open-source release notes",
            confidence="Low",
        )
    if "elastic" in lowered or "elasticsearch" in lowered:
        return requirement_record(
            supported_os="Windows / Linux versions supported by target Elastic release",
            supported_runtime="Bundled or vendor-supported Java runtime",
            supported_browser="Supported browser for Kibana/admin UI",
            supported_database="Cluster/index compatibility and rolling-upgrade path",
            supported_architecture="x64",
            source="Elastic compatibility documentation",
            confidence="Medium",
        )
    if "log4j" in lowered or "struts" in lowered or "spring framework" in lowered:
        return requirement_record(
            supported_os="Application server OS support must be confirmed from application owner/vendor",
            supported_runtime="Supported Java/JDK runtime required",
            supported_browser="Hosted application browser matrix if applicable",
            supported_database="Not applicable",
            supported_architecture="Application runtime architecture",
            source="Java framework release documentation",
            confidence="Low",
        )
    if "citrix adc" in lowered:
        return requirement_record(
            supported_os="Not applicable; network appliance firmware",
            supported_runtime="Not applicable",
            supported_browser="Microsoft Edge / Chrome for management console",
            supported_database="Not applicable",
            supported_architecture="Appliance / VPX image architecture",
            source="Citrix ADC firmware release notes",
            confidence="Medium",
        )
    if "pc backup" in lowered or "backup" in lowered:
        return requirement_record(
            supported_os="Client/server OS support per backup agent vendor documentation",
            supported_runtime="Vendor runtime prerequisites",
            supported_browser="Supported browser for admin console",
            supported_database="Backup catalog/repository compatibility",
            supported_architecture="x64",
            source="Backup product vendor documentation",
            confidence="Low",
        )
    if "kofax" in lowered or "powerpdf" in lowered:
        return requirement_record(
            supported_os="Windows desktop/server support must be confirmed from Kofax documentation",
            supported_runtime="Not available",
            supported_browser="Not applicable",
            supported_database="Not applicable",
            supported_architecture="x64",
            source="Kofax Power PDF vendor documentation",
            confidence="Low",
        )
    if any(token in lowered for token in [
        "7-zip",
        "7zip",
        "filezilla",
        "gimp",
        "inkscape",
        "winmerge",
        "vlc",
        "windirstat",
        "snagit",
        "pdf24",
        "kofax",
        "corel",
        "mindjet",
        "mindmanager",
        "davinci",
        "xmlpad",
        "xmlspy",
        "altova",
    ]):
        return endpoint_default
    return requirement_record(
        source="Vendor release notes mapped; compatibility requirements not extracted" if has_vendor_link else "Vendor source not mapped",
        confidence="Low" if has_vendor_link else "Not available",
    )


def legacy_compatibility_requirements(name: str, target_version: str = "") -> dict[str, str]:
    """Compatibility with older saved outputs."""
    requirements = compatibility_requirements(name, target_version)
    return {
        "Windows Version": requirements["Supported OS"],
        ".NET Version": requirements["Supported Runtime"],
        "Java Version": requirements["Supported Runtime"],
        "Browser Version": requirements["Supported Browser"],
        "Database Version": requirements["Database Dependency"],
        "OS Architecture": requirements["Supported Architecture"],
        "Requirement Source": requirements["Requirement Source"],
    }


def merge_vendor_requirements(
    name: str,
    fallback: dict[str, str],
    vendor: dict[str, Any] | None,
) -> dict[str, str]:
    if not vendor:
        return fallback
    merged = dict(fallback)
    for field in [
        "Supported OS",
        "Supported Runtime",
        "Supported Browser",
        "Database Dependency",
        "Supported Architecture",
        "Requirement Source",
        "Requirement Source URL",
        "Requirement Confidence",
        "Last Verified",
    ]:
        value = vendor.get(field)
        if value in (None, ""):
            continue
        vendor_value = str(value)
        fallback_value = str(fallback.get(field, ""))
        if _is_weak_requirement(vendor_value) and not _is_weak_requirement(fallback_value):
            continue
        lowered_name = name.lower()
        if "kofax" in lowered_name and field in {"Supported Runtime", "Supported Browser", "Database Dependency"}:
            continue
        if any(token in lowered_name for token in {"log4j", "struts", "spring framework"}) and field == "Database Dependency":
            continue
        merged[field] = vendor_value
    return merged


def _is_weak_requirement(value: str) -> bool:
    return value.strip().lower() in {"", "not available", "not applicable"}


def build_package_readiness(
    comparison: dict[str, Any],
    latest: dict[str, Any],
    vulnerabilities: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for name, record in comparison.items():
        latest_record = latest.get(name, {})
        current = record.get("current", {})
        target = record.get("latest", {})
        current_version = value(current, "Build Version", "version")
        latest_version = value(target, "Build Version", "version")
        current_cu = value(current, "Cumulative Update (CU)", "cu", default="")
        latest_cu = value(target, "Cumulative Update (CU)", "cu", default="")
        gap = version_gap(str(current_version), str(latest_version), str(current_cu), str(latest_cu))
        risk = value(vulnerabilities.get(name, {}), "risk_level", default="UNKNOWN").upper()
        needs_update = is_actionable_update(record)
        has_target = bool(latest_version and str(latest_version).lower() != "unknown")
        status, blocker = blocker_reason(
            name=name,
            current_version=str(current_version),
            latest_version=str(latest_version),
            current_cu=str(current_cu),
            latest_cu=str(latest_cu),
            gap=gap,
            risk=risk,
            has_target=has_target,
            needs_update=needs_update,
        )
        links = vendor_resource_links(name)
        rows[name] = {
            "Software Name": name,
            "Vendor": vendor_for(name),
            "Current Version": current_version,
            "Target Version": f"{latest_version} ({latest_cu})" if latest_cu else latest_version,
            "Package Readiness": status,
            "Upgrade Impact": "High" if risk in {"CRITICAL", "HIGH"} or gap == "Major Gap" else ("Medium" if needs_update else "Low"),
            "Owner": readiness_owner(name),
            "Installer Type": installer_type(name),
            "Vendor Information": value(latest_record, "source", default="Vendor source/cache"),
            "Release Notes": links["Release Notes"],
            "Download Link": links["Download Link"],
            "Checklist": {
                "Download package": has_target,
                "Verify checksum": False,
                "Verify signature": False,
                "Test installation": False,
                "Validate rollback": False,
                "Approve package": False,
            },
            "Blocker": blocker,
        }
    return rows


def current_environment_summary(metadata: dict[str, Any]) -> str:
    current = metadata.get("current_requirements") or metadata.get("current_environment") or {}
    if not isinstance(current, dict) or not current:
        return "Not provided in software.yml"
    labels = {
        "windows_version": "OS",
        "os": "OS",
        "dotnet_version": ".NET",
        "net_version": ".NET",
        "java_version": "Java",
        "browser_version": "Browser",
        "database_version": "Database",
        "architecture": "Architecture",
    }
    parts = []
    for key, label in labels.items():
        if current.get(key):
            parts.append(f"{label}: {current[key]}")
    return "; ".join(parts) if parts else "Provided, but no recognized requirement fields found"


def build_qa_validation(
    comparison: dict[str, Any],
    readiness: dict[str, Any],
    software_metadata: dict[str, Any] | None = None,
    vendor_requirements: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    software_metadata = software_metadata or {}
    vendor_requirements = vendor_requirements or {}
    for name, record in comparison.items():
        current = record.get("current", {})
        latest = record.get("latest", {})
        current_version = value(current, "Build Version", "version")
        latest_version = value(latest, "Build Version", "version")
        needs_update = is_actionable_update(record)
        ready = readiness.get(name, {})
        requirements = merge_vendor_requirements(
            name,
            compatibility_requirements(name, str(latest_version or "")),
            vendor_requirements.get(name),
        )
        current_environment = current_environment_summary(software_metadata.get(name, {}))
        result = "NOT TESTED" if needs_update else "BASELINE VERIFIED"
        installation = "Not Tested" if needs_update else "No Deployment Required"
        rows[name] = {
            "Software Name": name,
            "Package Name": name,
            "Package Version": latest_version or current_version,
            "Current Version": current_version,
            "Target Version": latest_version,
            "Installer Type": ready.get("Installer Type", installer_type(name)),
            "Publisher": vendor_for(name),
            "Installation Status": installation,
            "Test Result": result,
            "Compatibility Status": "Review Required" if needs_update else "Compatible",
            "Supported OS": requirements["Supported OS"],
            "Supported Runtime": requirements["Supported Runtime"],
            "Supported Browser": requirements["Supported Browser"],
            "Database Dependency": requirements["Database Dependency"],
            "Supported Architecture": requirements["Supported Architecture"],
            "Current Environment": current_environment,
            "Requirement Source": requirements.get("Requirement Source", "Built-in compatibility rule"),
            "Requirement Source URL": requirements.get("Requirement Source URL", ""),
            "Requirement Confidence": requirements.get("Requirement Confidence", "Not available"),
            "Last Verified": requirements.get("Last Verified", ""),
            "Functional Validation": {
                "Application Launch": False,
                "Service Running": False,
                "Registry Verified": False,
                "Files Installed": False,
                "Environment Variables": False,
                "License Activated": False,
            },
            "Test Notes": (
                "Pending QA validation."
                if needs_update
                else "Current installed version already matches approved target version. No QA deployment test required."
            ),
        }
    return rows


def build_compatibility_assessment(
    comparison: dict[str, Any],
    readiness: dict[str, Any],
    software_metadata: dict[str, Any] | None = None,
    vendor_requirements: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    qa_validation = build_qa_validation(comparison, readiness, software_metadata, vendor_requirements)
    return {
        name: {
            "Software Name": record["Software Name"],
            "Compatibility Status": record["Compatibility Status"],
            "Supported OS": record["Supported OS"],
            "Supported Runtime": record["Supported Runtime"],
            "Supported Browser": record["Supported Browser"],
            "Database Dependency": record["Database Dependency"],
            "Supported Architecture": record["Supported Architecture"],
            "Current Environment": record["Current Environment"],
            "Requirement Source": record["Requirement Source"],
            "Requirement Source URL": record["Requirement Source URL"],
            "Requirement Confidence": record["Requirement Confidence"],
            "Last Verified": record["Last Verified"],
        }
        for name, record in qa_validation.items()
    }


def save_workspace_outputs(
    comparison: dict[str, Any],
    latest: dict[str, Any],
    vulnerabilities: dict[str, Any],
    package_path: str,
    qa_path: str,
    software_metadata: dict[str, Any] | None = None,
    vendor_requirements: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    readiness = build_package_readiness(comparison, latest, vulnerabilities) if comparison else {}
    qa_validation = build_qa_validation(comparison, readiness, software_metadata, vendor_requirements) if comparison else {}
    for path, payload in ((package_path, readiness), (qa_path, qa_validation)):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
    return readiness, qa_validation
