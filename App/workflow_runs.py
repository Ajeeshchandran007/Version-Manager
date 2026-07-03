"""Workflow run history for team/release workflows.

Uses PostgreSQL when DATABASE_URL is configured, otherwise local SQLite.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from App.db import postgres_connection, using_postgres


def init_workflow_runs_db(db_path: Path) -> None:
    if using_postgres():
        _init_workflow_runs_postgres()
        return
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS workflow_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL UNIQUE,
                team TEXT NOT NULL,
                release_line TEXT NOT NULL,
                workflow_scope TEXT NOT NULL,
                category TEXT NOT NULL,
                status TEXT NOT NULL,
                triggered_by TEXT,
                triggered_by_role TEXT,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                duration_seconds REAL,
                total INTEGER DEFAULT 0,
                needs_update_count INTEGER DEFAULT 0,
                unknown_count INTEGER DEFAULT 0,
                email_sent INTEGER DEFAULT 0,
                error_message TEXT,
                summary_json TEXT NOT NULL
            )
        """)
        con.commit()


def record_workflow_run(
    db_path: Path,
    *,
    run_id: str,
    team: str,
    release_line: str,
    workflow_scope: str,
    category: str,
    status: str,
    triggered_by: str = "",
    triggered_by_role: str = "",
    started_at: str,
    ended_at: str,
    duration_seconds: float | None = None,
    total: int = 0,
    needs_update_count: int = 0,
    unknown_count: int = 0,
    email_sent: bool = False,
    error_message: str = "",
    summary: dict[str, Any] | None = None,
) -> None:
    init_workflow_runs_db(db_path)
    payload = json.dumps(summary or {}, default=str)
    values = (
        run_id,
        team,
        release_line,
        workflow_scope,
        category,
        status,
        triggered_by,
        triggered_by_role,
        started_at,
        ended_at,
        duration_seconds,
        int(total or 0),
        int(needs_update_count or 0),
        int(unknown_count or 0),
        bool(email_sent),
        error_message,
        payload,
    )
    if using_postgres():
        with postgres_connection() as con:
            with con.cursor() as cur:
                cur.execute(
                    """INSERT INTO workflow_runs
                       (run_id, team, release_line, workflow_scope, category, status,
                        triggered_by, triggered_by_role, started_at, ended_at,
                        duration_seconds, total, needs_update_count, unknown_count,
                        email_sent, error_message, summary_json)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (run_id) DO UPDATE SET
                        status=EXCLUDED.status,
                        ended_at=EXCLUDED.ended_at,
                        duration_seconds=EXCLUDED.duration_seconds,
                        total=EXCLUDED.total,
                        needs_update_count=EXCLUDED.needs_update_count,
                        unknown_count=EXCLUDED.unknown_count,
                        email_sent=EXCLUDED.email_sent,
                        error_message=EXCLUDED.error_message,
                        summary_json=EXCLUDED.summary_json""",
                    values,
                )
                con.commit()
        return
    sqlite_values = values[:14] + (1 if email_sent else 0,) + values[15:]
    with closing(sqlite3.connect(db_path)) as con:
        con.execute(
            """INSERT INTO workflow_runs
               (run_id, team, release_line, workflow_scope, category, status,
                triggered_by, triggered_by_role, started_at, ended_at,
                duration_seconds, total, needs_update_count, unknown_count,
                email_sent, error_message, summary_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(run_id) DO UPDATE SET
                status=excluded.status,
                ended_at=excluded.ended_at,
                duration_seconds=excluded.duration_seconds,
                total=excluded.total,
                needs_update_count=excluded.needs_update_count,
                unknown_count=excluded.unknown_count,
                email_sent=excluded.email_sent,
                error_message=excluded.error_message,
                summary_json=excluded.summary_json""",
            sqlite_values,
        )
        con.commit()


def list_workflow_runs(
    db_path: Path,
    *,
    team: str | None = None,
    release_line: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    init_workflow_runs_db(db_path)
    if using_postgres():
        where, params = _filters("%s", team, release_line)
        with postgres_connection() as con:
            with con.cursor() as cur:
                cur.execute(
                    f"""SELECT run_id, team, release_line, workflow_scope, category, status,
                               triggered_by, triggered_by_role, started_at, ended_at,
                               duration_seconds, total, needs_update_count, unknown_count,
                               email_sent, error_message, summary_json
                          FROM workflow_runs
                         {where}
                         ORDER BY id DESC
                         LIMIT %s""",
                    (*params, limit),
                )
                rows = cur.fetchall()
        return [_row_to_run(row) for row in rows]

    where, params = _filters("?", team, release_line)
    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"""SELECT run_id, team, release_line, workflow_scope, category, status,
                       triggered_by, triggered_by_role, started_at, ended_at,
                       duration_seconds, total, needs_update_count, unknown_count,
                       email_sent, error_message, summary_json
                  FROM workflow_runs
                 {where}
                 ORDER BY id DESC
                 LIMIT ?""",
            (*params, limit),
        ).fetchall()
    return [_row_to_run(dict(row)) for row in rows]


def _filters(placeholder: str, team: str | None, release_line: str | None) -> tuple[str, tuple[str, ...]]:
    clauses: list[str] = []
    params: list[str] = []
    if team:
        clauses.append(f"team={placeholder}")
        params.append(team)
    if release_line:
        clauses.append(f"release_line={placeholder}")
        params.append(release_line)
    if not clauses:
        return "", tuple()
    return "WHERE " + " AND ".join(clauses), tuple(params)


def _row_to_run(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["email_sent"] = bool(item.get("email_sent"))
    try:
        item["summary"] = json.loads(item.get("summary_json") or "{}")
    except json.JSONDecodeError:
        item["summary"] = {}
    item.pop("summary_json", None)
    return item


def _init_workflow_runs_postgres() -> None:
    with postgres_connection() as con:
        with con.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    id BIGSERIAL PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE,
                    team TEXT NOT NULL,
                    release_line TEXT NOT NULL,
                    workflow_scope TEXT NOT NULL,
                    category TEXT NOT NULL,
                    status TEXT NOT NULL,
                    triggered_by TEXT,
                    triggered_by_role TEXT,
                    started_at TEXT NOT NULL,
                    ended_at TEXT NOT NULL,
                    duration_seconds DOUBLE PRECISION,
                    total INTEGER DEFAULT 0,
                    needs_update_count INTEGER DEFAULT 0,
                    unknown_count INTEGER DEFAULT 0,
                    email_sent BOOLEAN DEFAULT FALSE,
                    error_message TEXT,
                    summary_json TEXT NOT NULL
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_workflow_runs_context ON workflow_runs (team, release_line, id DESC)")
            con.commit()
