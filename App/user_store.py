"""SQLite user store for Version Manager access control."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_USER_DB = BASE_DIR / "output" / "app_users.db"
ROLES = {"Admin", "Release Engineer", "QA Engineer"}


def normalize_team_scope(raw_scope: Any) -> list[str]:
    if raw_scope in (None, "", []):
        return ["*"]
    if isinstance(raw_scope, str):
        parts = raw_scope.split(",")
    elif isinstance(raw_scope, list):
        parts = raw_scope
    else:
        parts = [raw_scope]
    cleaned = [str(part).strip() for part in parts if str(part).strip()]
    return cleaned or ["*"]


def normalize_role(role: Any) -> str:
    value = str(role or "QA Engineer").strip().lower()
    if value == "admin":
        return "Admin"
    if value in {"operator", "packager", "release engineer", "release_engineer", "release-engineer"}:
        return "Release Engineer"
    if value in {"viewer", "tester", "qa", "qa engineer", "qa_engineer", "qa-engineer"}:
        return "QA Engineer"
    return "QA Engineer"


def init_user_db(db_path: Path = DEFAULT_USER_DB) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL,
                team_scope_json TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                last_login_at TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS user_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                target_username TEXT NOT NULL,
                details_json TEXT,
                ts TEXT NOT NULL
            )
        """)
        con.commit()


def seed_users_from_config(config_users: list[dict[str, Any]], db_path: Path = DEFAULT_USER_DB) -> None:
    init_user_db(db_path)
    with closing(sqlite3.connect(db_path)) as con:
        count = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if count:
        return
    for user in config_users:
        username = str(user.get("username") or "").strip()
        password = str(user.get("password") or "").strip()
        if not username or not password:
            continue
        upsert_user(
            username=username,
            password=password,
            display_name=str(user.get("display_name") or username),
            role=normalize_role(user.get("role")),
            team_scope=normalize_team_scope(user.get("team_scope")),
            active=True,
            actor="system-seed",
            db_path=db_path,
        )


def list_users(db_path: Path = DEFAULT_USER_DB, include_inactive: bool = True) -> list[dict[str, Any]]:
    init_user_db(db_path)
    sql = "SELECT username, display_name, role, team_scope_json, active, created_at, updated_at, last_login_at FROM users"
    if not include_inactive:
        sql += " WHERE active=1"
    sql += " ORDER BY username"
    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(sql).fetchall()
    return [_row_to_user(row) for row in rows]


def authenticate_user(username: str, password: str, db_path: Path = DEFAULT_USER_DB) -> dict[str, Any] | None:
    init_user_db(db_path)
    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM users WHERE lower(username)=lower(?) AND active=1", (username.strip(),)).fetchone()
        if not row:
            return None
        if not _verify_password(password, row["salt"], row["password_hash"]):
            return None
        now = _now()
        con.execute("UPDATE users SET last_login_at=? WHERE username=?", (now, row["username"]))
        con.execute(
            "INSERT INTO user_audit (actor, action, target_username, details_json, ts) VALUES (?, ?, ?, ?, ?)",
            (row["username"], "login", row["username"], None, now),
        )
        con.commit()
    user = _row_to_user(row)
    user["last_login_at"] = now
    return user


def get_user(username: str, db_path: Path = DEFAULT_USER_DB) -> dict[str, Any] | None:
    init_user_db(db_path)
    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM users WHERE lower(username)=lower(?)", (username.strip(),)).fetchone()
    return _row_to_user(row) if row else None


def upsert_user(
    *,
    username: str,
    password: str | None,
    display_name: str,
    role: str,
    team_scope: list[str] | str,
    active: bool,
    actor: str,
    db_path: Path = DEFAULT_USER_DB,
) -> dict[str, Any]:
    init_user_db(db_path)
    username = username.strip()
    if not username:
        raise ValueError("Username is required.")
    role = normalize_role(role)
    scope = normalize_team_scope(team_scope)
    now = _now()

    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        existing = con.execute("SELECT * FROM users WHERE lower(username)=lower(?)", (username,)).fetchone()
        if existing:
            if password:
                salt, password_hash = _hash_password(password)
                con.execute(
                    """UPDATE users
                       SET password_hash=?, salt=?, display_name=?, role=?, team_scope_json=?,
                           active=?, updated_at=?
                       WHERE username=?""",
                    (password_hash, salt, display_name, role, json.dumps(scope), int(active), now, existing["username"]),
                )
            else:
                con.execute(
                    """UPDATE users
                       SET display_name=?, role=?, team_scope_json=?, active=?, updated_at=?
                       WHERE username=?""",
                    (display_name, role, json.dumps(scope), int(active), now, existing["username"]),
                )
            target = existing["username"]
            action = "update_user"
        else:
            if not password:
                raise ValueError("Password is required for new users.")
            salt, password_hash = _hash_password(password)
            con.execute(
                """INSERT INTO users
                   (username, password_hash, salt, display_name, role, team_scope_json, active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (username, password_hash, salt, display_name or username, role, json.dumps(scope), int(active), now, now),
            )
            target = username
            action = "create_user"
        con.execute(
            "INSERT INTO user_audit (actor, action, target_username, details_json, ts) VALUES (?, ?, ?, ?, ?)",
            (actor, action, target, json.dumps({"role": role, "team_scope": scope, "active": active}), now),
        )
        con.commit()
    return get_user(username, db_path) or {}


def set_user_active(username: str, active: bool, actor: str, db_path: Path = DEFAULT_USER_DB) -> None:
    init_user_db(db_path)
    now = _now()
    with closing(sqlite3.connect(db_path)) as con:
        con.execute("UPDATE users SET active=?, updated_at=? WHERE lower(username)=lower(?)", (int(active), now, username.strip()))
        con.execute(
            "INSERT INTO user_audit (actor, action, target_username, details_json, ts) VALUES (?, ?, ?, ?, ?)",
            (actor, "activate_user" if active else "deactivate_user", username, json.dumps({"active": active}), now),
        )
        con.commit()


def list_user_audit(db_path: Path = DEFAULT_USER_DB, limit: int = 100) -> list[dict[str, Any]]:
    init_user_db(db_path)
    with closing(sqlite3.connect(db_path)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT actor, action, target_username, details_json, ts FROM user_audit ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def _row_to_user(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "username": row["username"],
        "display_name": row["display_name"] or row["username"],
        "role": normalize_role(row["role"]),
        "team_scope": normalize_team_scope(json.loads(row["team_scope_json"] or "[]")),
        "active": bool(row["active"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_login_at": row["last_login_at"],
    }


def _hash_password(password: str) -> tuple[str, str]:
    salt = os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 200_000)
    return salt, digest.hex()


def _verify_password(password: str, salt: str, expected_hash: str) -> bool:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 200_000)
    return hmac.compare_digest(digest.hex(), expected_hash)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
