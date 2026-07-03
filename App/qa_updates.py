"""Concurrent QA validation updates.

The rest of the app still consumes ``qa_validation.json`` as a report artifact,
but QA edits are persisted row-by-row in PostgreSQL when DATABASE_URL is set,
or SQLite locally otherwise. This gives us optimistic locking and prevents one
QA engineer's save from overwriting another engineer's work on a different row.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from App.db import postgres_connection, using_postgres


class QAUpdateConflict(RuntimeError):
    """Raised when a user tries to save over a newer QA row revision."""


def safe_int(raw: Any, default: int = 0) -> int:
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return default


def init_qa_db(db_path: Path) -> None:
    if using_postgres():
        _init_qa_postgres()
        return
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS qa_validation_rows (
                team TEXT NOT NULL,
                release_line TEXT NOT NULL,
                software_name TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 0,
                updated_by TEXT,
                updated_at TEXT,
                PRIMARY KEY (team, release_line, software_name)
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS qa_update_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team TEXT NOT NULL,
                release_line TEXT NOT NULL,
                software_name TEXT NOT NULL,
                revision INTEGER NOT NULL,
                updated_by TEXT,
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
        """)
        con.commit()


def load_qa_validation(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def sync_json_to_db(
    db_path: Path,
    qa_file: Path,
    *,
    team: str,
    release_line: str,
) -> dict[str, Any]:
    """Import JSON rows into SQLite without overwriting newer DB rows."""
    init_qa_db(db_path)
    data = load_qa_validation(qa_file)
    if using_postgres():
        with postgres_connection() as con:
            with con.cursor() as cur:
                for software_name, record in data.items():
                    revision = safe_int(record.get("QA Revision"), 0)
                    cur.execute(
                        """SELECT revision FROM qa_validation_rows
                           WHERE team=%s AND release_line=%s AND software_name=%s""",
                        (team, release_line, software_name),
                    )
                    if cur.fetchone():
                        continue
                    enriched = dict(record)
                    enriched["QA Revision"] = revision
                    cur.execute(
                        """INSERT INTO qa_validation_rows
                           (team, release_line, software_name, payload_json, revision, updated_by, updated_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (
                            team,
                            release_line,
                            software_name,
                            json.dumps(enriched),
                            revision,
                            str(enriched.get("Last QA Updated By") or ""),
                            str(enriched.get("Last QA Update") or ""),
                        ),
                    )
                con.commit()
        return export_db_to_json(db_path, qa_file, team=team, release_line=release_line)
    with closing(sqlite3.connect(db_path)) as con:
        for software_name, record in data.items():
            revision = safe_int(record.get("QA Revision"), 0)
            row = con.execute(
                """SELECT revision FROM qa_validation_rows
                   WHERE team=? AND release_line=? AND software_name=?""",
                (team, release_line, software_name),
            ).fetchone()
            if row:
                continue
            enriched = dict(record)
            enriched["QA Revision"] = revision
            con.execute(
                """INSERT INTO qa_validation_rows
                   (team, release_line, software_name, payload_json, revision, updated_by, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    team,
                    release_line,
                    software_name,
                    json.dumps(enriched),
                    revision,
                    str(enriched.get("Last QA Updated By") or ""),
                    str(enriched.get("Last QA Update") or ""),
                ),
            )
        con.commit()
    return export_db_to_json(db_path, qa_file, team=team, release_line=release_line)


def export_db_to_json(
    db_path: Path,
    qa_file: Path,
    *,
    team: str,
    release_line: str,
) -> dict[str, Any]:
    init_qa_db(db_path)
    rows: dict[str, Any] = {}
    if using_postgres():
        with postgres_connection() as con:
            with con.cursor() as cur:
                cur.execute(
                    """SELECT software_name, payload_json, revision, updated_by, updated_at
                       FROM qa_validation_rows
                       WHERE team=%s AND release_line=%s
                       ORDER BY software_name""",
                    (team, release_line),
                )
                db_rows = cur.fetchall()
        for row in db_rows:
            payload = json.loads(row["payload_json"])
            payload["QA Revision"] = int(row["revision"])
            if row["updated_by"]:
                payload["Last QA Updated By"] = row["updated_by"]
            if row["updated_at"]:
                payload["Last QA Update"] = row["updated_at"]
            rows[row["software_name"]] = payload
        qa_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = qa_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        tmp.replace(qa_file)
        return rows
    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        for row in con.execute(
            """SELECT software_name, payload_json, revision, updated_by, updated_at
               FROM qa_validation_rows
               WHERE team=? AND release_line=?
               ORDER BY software_name""",
            (team, release_line),
        ):
            payload = json.loads(row["payload_json"])
            payload["QA Revision"] = int(row["revision"])
            if row["updated_by"]:
                payload["Last QA Updated By"] = row["updated_by"]
            if row["updated_at"]:
                payload["Last QA Update"] = row["updated_at"]
            rows[row["software_name"]] = payload
    qa_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = qa_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    tmp.replace(qa_file)
    return rows


def save_qa_row_update(
    qa_file: Path,
    software_name: str,
    updates: dict[str, Any],
    *,
    expected_revision: int,
    updated_by: str,
    evidence_file: Any | None = None,
    db_path: Path | None = None,
    team: str = "Default",
    release_line: str = "Working / Latest",
) -> dict[str, Any]:
    db_path = db_path or qa_file.parent / "app_state.db"
    sync_json_to_db(db_path, qa_file, team=team, release_line=release_line)
    evidence_path = _save_evidence(qa_file.parent / "qa_evidence", software_name, evidence_file)
    updated_at = datetime.now().isoformat(timespec="seconds")

    if using_postgres():
        with postgres_connection() as con:
            with con.cursor() as cur:
                cur.execute(
                    """SELECT payload_json, revision FROM qa_validation_rows
                       WHERE team=%s AND release_line=%s AND software_name=%s
                       FOR UPDATE""",
                    (team, release_line, software_name),
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError(f"QA record not found for {software_name}")

                current_revision = int(row["revision"])
                if current_revision != expected_revision:
                    raise QAUpdateConflict(
                        f"{software_name} was updated by another user. Reload the QA page before saving."
                    )

                record = json.loads(row["payload_json"])
                record.update(updates)
                record["Manual QA Updated"] = True
                record["Last QA Update"] = updated_at
                record["Last QA Updated By"] = updated_by
                record["QA Revision"] = current_revision + 1
                if evidence_path:
                    record["Evidence File"] = evidence_path

                payload_json = json.dumps(record)
                cur.execute(
                    """UPDATE qa_validation_rows
                       SET payload_json=%s, revision=%s, updated_by=%s, updated_at=%s
                       WHERE team=%s AND release_line=%s AND software_name=%s""",
                    (
                        payload_json,
                        current_revision + 1,
                        updated_by,
                        updated_at,
                        team,
                        release_line,
                        software_name,
                    ),
                )
                cur.execute(
                    """INSERT INTO qa_update_audit
                       (team, release_line, software_name, revision, updated_by, updated_at, payload_json)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (team, release_line, software_name, current_revision + 1, updated_by, updated_at, payload_json),
                )
                con.commit()

        export_db_to_json(db_path, qa_file, team=team, release_line=release_line)
        return record

    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            """SELECT payload_json, revision FROM qa_validation_rows
               WHERE team=? AND release_line=? AND software_name=?""",
            (team, release_line, software_name),
        ).fetchone()
        if not row:
            raise ValueError(f"QA record not found for {software_name}")

        current_revision = int(row["revision"])
        if current_revision != expected_revision:
            raise QAUpdateConflict(
                f"{software_name} was updated by another user. Reload the QA page before saving."
            )

        record = json.loads(row["payload_json"])
        record.update(updates)
        record["Manual QA Updated"] = True
        record["Last QA Update"] = updated_at
        record["Last QA Updated By"] = updated_by
        record["QA Revision"] = current_revision + 1
        if evidence_path:
            record["Evidence File"] = evidence_path

        payload_json = json.dumps(record)
        con.execute(
            """UPDATE qa_validation_rows
               SET payload_json=?, revision=?, updated_by=?, updated_at=?
               WHERE team=? AND release_line=? AND software_name=?""",
            (
                payload_json,
                current_revision + 1,
                updated_by,
                updated_at,
                team,
                release_line,
                software_name,
            ),
        )
        con.execute(
            """INSERT INTO qa_update_audit
               (team, release_line, software_name, revision, updated_by, updated_at, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (team, release_line, software_name, current_revision + 1, updated_by, updated_at, payload_json),
        )
        con.commit()

    export_db_to_json(db_path, qa_file, team=team, release_line=release_line)
    return record


def build_qa_update_payload(
    installation_status: str,
    test_result: str,
    test_case_count: int,
    test_cases_passed: int,
    test_cases_failed: int,
    test_cases_blocked: int,
    notes: str,
    test_date: Any,
    existing_notes: str = "",
) -> dict[str, Any]:
    test_case_count = max(safe_int(test_case_count), 0)
    test_cases_passed = max(safe_int(test_cases_passed), 0)
    test_cases_failed = max(safe_int(test_cases_failed), 0)
    test_cases_blocked = max(safe_int(test_cases_blocked), 0)
    test_cases_executed = test_cases_passed + test_cases_failed + test_cases_blocked
    if test_case_count:
        test_cases_executed = min(test_cases_executed, test_case_count)
        coverage_pct = round((test_cases_executed / test_case_count) * 100, 1)
        coverage_label = f"{coverage_pct:g}%"
    else:
        coverage_label = "Not Required"

    payload: dict[str, Any] = {
        "Installation Status": installation_status,
        "Test Result": test_result,
        "Test Case Count": test_case_count,
        "Test Cases Passed": test_cases_passed,
        "Test Cases Failed": test_cases_failed,
        "Test Cases Blocked / Not Tested": test_cases_blocked,
        "Test Cases Executed": test_cases_executed,
        "Test Case Coverage %": coverage_label,
        "Test Notes": notes.strip() or existing_notes,
        "Test Date": str(test_date),
    }

    if test_result == "PASS":
        payload["Functional Validation"] = {
            "Application Launch": True,
            "Service Running": True,
            "Registry Verified": True,
            "Files Installed": True,
            "Environment Variables": True,
            "License Activated": True,
        }
    elif test_result == "FAIL":
        payload["Functional Validation"] = {}

    return payload


def _init_qa_postgres() -> None:
    with postgres_connection() as con:
        with con.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS qa_validation_rows (
                    team TEXT NOT NULL,
                    release_line TEXT NOT NULL,
                    software_name TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    updated_by TEXT,
                    updated_at TEXT,
                    PRIMARY KEY (team, release_line, software_name)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS qa_update_audit (
                    id BIGSERIAL PRIMARY KEY,
                    team TEXT NOT NULL,
                    release_line TEXT NOT NULL,
                    software_name TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_by TEXT,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
            """)
            con.commit()


def _save_evidence(evidence_dir: Path, software_name: str, evidence_file: Any | None) -> str:
    if evidence_file is None:
        return ""
    evidence_dir.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(ch if ch.isalnum() or ch in {".", "-", "_"} else "_" for ch in evidence_file.name)
    software_slug = software_name.replace(" ", "_")
    target = evidence_dir / f"{software_slug}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_name}"
    target.write_bytes(evidence_file.getbuffer())
    return str(target)
