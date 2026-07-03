"""SQLite-backed team/release workflow locks."""
from __future__ import annotations

import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator


class WorkflowAlreadyRunning(RuntimeError):
    """Raised when a workflow lock already exists for the same scope."""


def init_lock_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS workflow_locks (
                team TEXT NOT NULL,
                release_line TEXT NOT NULL,
                scope TEXT NOT NULL,
                owner TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (team, release_line, scope)
            )
        """)
        con.commit()


@contextmanager
def workflow_lock(
    db_path: Path,
    *,
    team: str,
    release: str,
    scope: str,
    owner: str,
    stale_after_seconds: int = 60 * 60,
) -> Iterator[None]:
    init_lock_db(db_path)
    _remove_stale_locks(db_path, stale_after_seconds)
    created_at = time.time()
    try:
        with closing(sqlite3.connect(db_path)) as con:
            con.execute("BEGIN IMMEDIATE")
            existing = _get_lock(con, team, release, scope)
            if existing:
                raise WorkflowAlreadyRunning(_lock_message(existing, team, release, scope))
            con.execute(
                """INSERT INTO workflow_locks
                   (team, release_line, scope, owner, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (team, release, scope, owner, created_at),
            )
            con.commit()
    except sqlite3.IntegrityError as exc:
        with closing(sqlite3.connect(db_path)) as con:
            existing = _get_lock(con, team, release, scope)
        raise WorkflowAlreadyRunning(_lock_message(existing or {}, team, release, scope)) from exc

    try:
        yield
    finally:
        with closing(sqlite3.connect(db_path)) as con:
            con.execute(
                "DELETE FROM workflow_locks WHERE team=? AND release_line=? AND scope=? AND owner=?",
                (team, release, scope, owner),
            )
            con.commit()


def _remove_stale_locks(db_path: Path, stale_after_seconds: int) -> None:
    cutoff = time.time() - stale_after_seconds
    with closing(sqlite3.connect(db_path)) as con:
        con.execute("DELETE FROM workflow_locks WHERE created_at < ?", (cutoff,))
        con.commit()


def _get_lock(
    con: sqlite3.Connection,
    team: str,
    release: str,
    scope: str,
) -> dict[str, Any] | None:
    con.row_factory = sqlite3.Row
    row = con.execute(
        """SELECT team, release_line, scope, owner, created_at
           FROM workflow_locks
           WHERE team=? AND release_line=? AND scope=?""",
        (team, release, scope),
    ).fetchone()
    return dict(row) if row else None


def _lock_message(existing: dict[str, Any], team: str, release: str, scope: str) -> str:
    owner = existing.get("owner") or "another user"
    locked_scope = existing.get("scope") or scope
    locked_team = existing.get("team") or team
    locked_release = existing.get("release_line") or release
    return (
        f"{locked_scope} is already running for {locked_team} / {locked_release} "
        f"by {owner}. Please wait for it to finish."
    )
