# agent/memory.py
"""
SQLite-backed memory layer.
Stores run history, confidence metadata, and a full audit log
so the agent can reason about drift and past failures.
"""
import sqlite3
import json
import datetime
from pathlib import Path
from Utils.utils import logger

DB_PATH = Path("output/agent_memory.db")


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS run_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      TEXT NOT NULL,
            software    TEXT NOT NULL,
            category    TEXT NOT NULL,
            build_ver   TEXT,
            cu_ver      TEXT,
            source      TEXT,
            confidence  TEXT CHECK(confidence IN ('HIGH','LOW')),
            needs_update INTEGER,
            ts          TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      TEXT NOT NULL,
            step        TEXT NOT NULL,
            actor       TEXT NOT NULL,   -- 'agent' | 'tool:<name>'
            payload     TEXT,            -- JSON
            ts          TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS failure_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            software    TEXT NOT NULL,
            host        TEXT,
            reason      TEXT,
            ts          TEXT NOT NULL
        );
        """)
    logger.info("Memory DB initialised.")


def save_run_result(
    run_id: str,
    software: str,
    category: str,
    build_ver: str | None,
    cu_ver: str | None,
    source: str,
    needs_update: bool,
) -> None:
    confidence = "HIGH" if source == "live server" else "LOW"
    with _conn() as con:
        con.execute(
            """INSERT INTO run_history
               (run_id,software,category,build_ver,cu_ver,source,confidence,needs_update,ts)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (run_id, software, category, build_ver, cu_ver,
             source, confidence, int(needs_update),
             datetime.datetime.utcnow().isoformat()),
        )


def log_audit(run_id: str, step: str, actor: str, payload: dict | None = None) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO audit_log (run_id,step,actor,payload,ts) VALUES (?,?,?,?,?)",
            (run_id, step, actor,
             json.dumps(payload) if payload else None,
             datetime.datetime.utcnow().isoformat()),
        )


def log_failure(software: str, host: str | None, reason: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO failure_log (software,host,reason,ts) VALUES (?,?,?,?)",
            (software, host, reason, datetime.datetime.utcnow().isoformat()),
        )


def get_baseline(software: str) -> dict | None:
    """Last known-good HIGH-confidence result."""
    with _conn() as con:
        row = con.execute(
            """SELECT build_ver, cu_ver, ts FROM run_history
               WHERE software=? AND confidence='HIGH'
               ORDER BY id DESC LIMIT 1""",
            (software,),
        ).fetchone()
    return dict(row) if row else None


def get_recent_failures(software: str, limit: int = 3) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT host,reason,ts FROM failure_log WHERE software=? ORDER BY id DESC LIMIT ?",
            (software, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_run_history(software: str, limit: int = 10) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            """SELECT build_ver,cu_ver,source,confidence,needs_update,ts
               FROM run_history WHERE software=? ORDER BY id DESC LIMIT ?""",
            (software, limit),
        ).fetchall()
    return [dict(r) for r in rows]