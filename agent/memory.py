# agent/memory.py
"""
Agent memory layer.

Uses PostgreSQL when DATABASE_URL is configured, otherwise local SQLite.
Stores run history, confidence metadata, and a full audit log so the agent can
reason about drift and past failures.
"""
from __future__ import annotations

import json
import datetime
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from App.db import postgres_connection, using_postgres
from Utils.utils import logger

DB_PATH = Path("output/agent_memory.db")


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    if using_postgres():
        _init_postgres()
        logger.info("Memory DB initialised using PostgreSQL.")
        return
    with closing(_conn()) as con:
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
        con.commit()
    logger.info("Memory DB initialised using SQLite.")


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
    init_db()
    if using_postgres():
        with postgres_connection() as con:
            with con.cursor() as cur:
                cur.execute(
                    """INSERT INTO run_history
                       (run_id,software,category,build_ver,cu_ver,source,confidence,needs_update,ts)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        run_id,
                        software,
                        category,
                        build_ver,
                        cu_ver,
                        source,
                        confidence,
                        bool(needs_update),
                        _utc_now(),
                    ),
                )
                con.commit()
        return
    with closing(_conn()) as con:
        con.execute(
            """INSERT INTO run_history
               (run_id,software,category,build_ver,cu_ver,source,confidence,needs_update,ts)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (run_id, software, category, build_ver, cu_ver,
             source, confidence, int(needs_update),
             _utc_now()),
        )
        con.commit()


def log_audit(run_id: str, step: str, actor: str, payload: dict | None = None) -> None:
    init_db()
    payload_json = json.dumps(payload) if payload else None
    if using_postgres():
        with postgres_connection() as con:
            with con.cursor() as cur:
                cur.execute(
                    "INSERT INTO audit_log (run_id,step,actor,payload,ts) VALUES (%s,%s,%s,%s,%s)",
                    (run_id, step, actor, payload_json, _utc_now()),
                )
                con.commit()
        return
    with closing(_conn()) as con:
        con.execute(
            "INSERT INTO audit_log (run_id,step,actor,payload,ts) VALUES (?,?,?,?,?)",
            (run_id, step, actor, payload_json, _utc_now()),
        )
        con.commit()


def log_failure(software: str, host: str | None, reason: str) -> None:
    init_db()
    if using_postgres():
        with postgres_connection() as con:
            with con.cursor() as cur:
                cur.execute(
                    "INSERT INTO failure_log (software,host,reason,ts) VALUES (%s,%s,%s,%s)",
                    (software, host, reason, _utc_now()),
                )
                con.commit()
        return
    with closing(_conn()) as con:
        con.execute(
            "INSERT INTO failure_log (software,host,reason,ts) VALUES (?,?,?,?)",
            (software, host, reason, _utc_now()),
        )
        con.commit()


def get_baseline(software: str) -> dict | None:
    """Last known-good HIGH-confidence result."""
    init_db()
    if using_postgres():
        with postgres_connection() as con:
            with con.cursor() as cur:
                cur.execute(
                    """SELECT build_ver, cu_ver, ts FROM run_history
                       WHERE software=%s AND confidence='HIGH'
                       ORDER BY id DESC LIMIT 1""",
                    (software,),
                )
                row = cur.fetchone()
        return dict(row) if row else None
    with closing(_conn()) as con:
        row = con.execute(
            """SELECT build_ver, cu_ver, ts FROM run_history
               WHERE software=? AND confidence='HIGH'
               ORDER BY id DESC LIMIT 1""",
            (software,),
        ).fetchone()
    return dict(row) if row else None


def get_recent_failures(software: str, limit: int = 3) -> list[dict]:
    init_db()
    if using_postgres():
        with postgres_connection() as con:
            with con.cursor() as cur:
                cur.execute(
                    "SELECT host,reason,ts FROM failure_log WHERE software=%s ORDER BY id DESC LIMIT %s",
                    (software, limit),
                )
                rows = cur.fetchall()
        return [dict(r) for r in rows]
    with closing(_conn()) as con:
        rows = con.execute(
            "SELECT host,reason,ts FROM failure_log WHERE software=? ORDER BY id DESC LIMIT ?",
            (software, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_run_history(software: str, limit: int = 10) -> list[dict]:
    init_db()
    if using_postgres():
        with postgres_connection() as con:
            with con.cursor() as cur:
                cur.execute(
                    """SELECT build_ver,cu_ver,source,confidence,needs_update,ts
                       FROM run_history WHERE software=%s ORDER BY id DESC LIMIT %s""",
                    (software, limit),
                )
                rows = cur.fetchall()
        return [_row_to_history(r) for r in rows]
    with closing(_conn()) as con:
        rows = con.execute(
            """SELECT build_ver,cu_ver,source,confidence,needs_update,ts
               FROM run_history WHERE software=? ORDER BY id DESC LIMIT ?""",
            (software, limit),
        ).fetchall()
    return [_row_to_history(r) for r in rows]


def _utc_now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _row_to_history(row: Any) -> dict:
    item = dict(row)
    item["needs_update"] = bool(item.get("needs_update"))
    return item


def _init_postgres() -> None:
    with postgres_connection() as con:
        with con.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS run_history (
                    id BIGSERIAL PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    software TEXT NOT NULL,
                    category TEXT NOT NULL,
                    build_ver TEXT,
                    cu_ver TEXT,
                    source TEXT,
                    confidence TEXT CHECK(confidence IN ('HIGH','LOW')),
                    needs_update BOOLEAN,
                    ts TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id BIGSERIAL PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    step TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    payload TEXT,
                    ts TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS failure_log (
                    id BIGSERIAL PRIMARY KEY,
                    software TEXT NOT NULL,
                    host TEXT,
                    reason TEXT,
                    ts TEXT NOT NULL
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_run_history_software_id ON run_history (software, id DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_failure_log_software_id ON failure_log (software, id DESC)")
            con.commit()
