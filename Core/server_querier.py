# Core/server_querier.py
"""
Queries live servers for installed software versions.
Supports SSH (Linux/Windows via paramiko) and HTTP (REST APIs like ElasticSearch).
Falls back gracefully — caller decides what to do on failure.
"""
import re
import json
import asyncio
import httpx
import paramiko
from Utils.utils import logger, load_config


class ServerQuerier:
    def __init__(self):
        config = load_config()
        self.server_configs: dict = config.get("servers", {})

    async def fetch(self, software_name: str) -> dict | None:
        """
        Try to get installed version from the live server.
        Returns { Build Version, Cumulative Update (CU) } or None if unreachable/unconfigured.
        """
        cfg = self.server_configs.get(software_name)
        if not cfg:
            logger.info(f"ServerQuerier: no server config for '{software_name}', skipping.")
            return None

        method = cfg.get("method", "ssh").lower()

        if method == "ssh":
            return await self._query_ssh(software_name, cfg)
        elif method == "http":
            return await self._query_http(software_name, cfg)
        else:
            logger.warning(f"ServerQuerier: unknown method '{method}' for {software_name}")
            return None

    # ------------------------------------------------------------------
    # SSH Query
    # ------------------------------------------------------------------
    async def _query_ssh(self, software_name: str, cfg: dict) -> dict | None:
        """Run a remote command over SSH and parse the version from stdout."""
        host     = cfg.get("host")
        user     = cfg.get("user")
        key_file = cfg.get("key_file")
        password = cfg.get("password")       # alternative to key_file
        command  = cfg.get("command")
        port     = cfg.get("port", 22)

        if not all([host, user, command]):
            logger.error(f"ServerQuerier SSH: missing host/user/command for {software_name}")
            return None

        logger.info(f"ServerQuerier SSH: connecting to {host} for '{software_name}'")
        try:
            # Run blocking paramiko call in a thread so we don't block event loop
            output = await asyncio.to_thread(
                _ssh_run, host, port, user, key_file, password, command
            )
            if output is None:
                return None

            logger.info(f"ServerQuerier SSH raw output [{software_name}]: {output[:200]}")
            return _parse_version_from_output(software_name, output)

        except Exception as e:
            logger.error(f"ServerQuerier SSH error for {software_name}: {e}")
            return None

    # ------------------------------------------------------------------
    # HTTP Query (e.g. ElasticSearch GET /)
    # ------------------------------------------------------------------
    async def _query_http(self, software_name: str, cfg: dict) -> dict | None:
        url     = cfg.get("url")
        headers = cfg.get("headers", {})
        timeout = cfg.get("timeout", 10)

        if not url:
            logger.error(f"ServerQuerier HTTP: missing url for {software_name}")
            return None

        logger.info(f"ServerQuerier HTTP: GET {url} for '{software_name}'")
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            logger.info(f"ServerQuerier HTTP raw response [{software_name}]: {str(data)[:200]}")
            return _parse_http_response(software_name, data)

        except Exception as e:
            logger.error(f"ServerQuerier HTTP error for {software_name}: {e}")
            return None


# ------------------------------------------------------------------
# Blocking SSH helper (runs in thread via asyncio.to_thread)
# ------------------------------------------------------------------
def _ssh_run(
    host: str, port: int, user: str,
    key_file: str | None, password: str | None, command: str
) -> str | None:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        connect_kwargs = dict(hostname=host, port=port, username=user, timeout=15)
        if key_file:
            connect_kwargs["key_filename"] = key_file
        elif password:
            connect_kwargs["password"] = password

        client.connect(**connect_kwargs)
        _, stdout, stderr = client.exec_command(command, timeout=30)
        output = stdout.read().decode().strip()
        err    = stderr.read().decode().strip()

        if err:
            logger.warning(f"SSH stderr: {err}")
        return output or None

    except Exception as e:
        logger.error(f"SSH connection/command error ({host}): {e}")
        return None
    finally:
        client.close()


# ------------------------------------------------------------------
# Version parsers — one per software type
# ------------------------------------------------------------------
def _parse_version_from_output(software_name: str, output: str) -> dict:
    """Route to the right parser based on software name."""
    name = software_name.lower()
    result = {"Build Version": None, "Cumulative Update (CU)": None, "source": "live server"}

    if "sql server" in name:
        # Example: Microsoft SQL Server 2019 (RTM-CU18) (KB5017593) - 15.0.4261.1
        build = re.search(r"(\d+\.\d+\.\d+\.\d+)", output)
        cu    = re.search(r"RTM-(CU\d+)", output, re.IGNORECASE)
        if build: result["Build Version"]          = build.group(1)
        if cu:    result["Cumulative Update (CU)"] = cu.group(1).upper()

    elif "exchange" in name:
        # Example: 15.2.1258.12
        build = re.search(r"(\d+\.\d+\.\d+\.\d+)", output)
        cu    = re.search(r"(CU\d+)", output, re.IGNORECASE)
        if build: result["Build Version"]          = build.group(1)
        if cu:    result["Cumulative Update (CU)"] = cu.group(1).upper()

    elif "libcurl" in name or "curl" in name:
        # Example: curl 8.1.2 (x86_64-pc-linux-gnu)
        build = re.search(r"curl\s+(\d+\.\d+\.\d+)", output, re.IGNORECASE)
        if build: result["Build Version"] = build.group(1)

    elif "openssl" in name:
        # Example: OpenSSL 3.0.2 15 Mar 2022
        build = re.search(r"OpenSSL\s+(\d+\.\d+\.\d+)", output, re.IGNORECASE)
        if build: result["Build Version"] = build.group(1)

    elif "elasticsearch" in name:
        # Handled by HTTP parser below
        pass

    elif "domino" in name or "notes" in name:
        # Example: Release 12.0.2
        build = re.search(r"Release\s+([\d.]+)", output, re.IGNORECASE)
        if build: result["Build Version"] = build.group(1)

    elif "outlook" in name:
        # Example: 16.0.14326.20454
        build = re.search(r"(\d+\.\d+\.\d+\.\d+)", output)
        if build: result["Build Version"] = build.group(1)

    elif "edge" in name:
        # Example: 114.0.1823.51
        build = re.search(r"(\d+\.\d+\.\d+\.\d+)", output)
        if build: result["Build Version"] = build.group(1)

    else:
        # Generic: grab first version-like pattern
        build = re.search(r"(\d+\.\d+[\.\d]*)", output)
        if build: result["Build Version"] = build.group(1)

    logger.info(f"Parsed from live server [{software_name}]: {result}")
    return result


def _parse_http_response(software_name: str, data: dict) -> dict:
    """Parse HTTP JSON responses (e.g. ElasticSearch GET /)."""
    result = {"Build Version": None, "Cumulative Update (CU)": None, "source": "live server"}
    name = software_name.lower()

    if "elasticsearch" in name:
        # { "version": { "number": "8.13.0", "build_flavor": "default", ... } }
        version = data.get("version", {})
        result["Build Version"] = version.get("number")

    return result