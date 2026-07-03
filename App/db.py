"""Database backend selection for local SQLite and hosted PostgreSQL."""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    try:
        import streamlit as st

        return str(st.secrets.get("DATABASE_URL", "")).strip()
    except Exception:
        return ""


def using_postgres() -> bool:
    url = database_url()
    return url.startswith(("postgresql://", "postgres://"))


@contextmanager
def postgres_connection() -> Iterator[object]:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover - only hit when DATABASE_URL is configured without dependency
        raise RuntimeError("DATABASE_URL is configured, but psycopg is not installed.") from exc

    with psycopg.connect(database_url(), row_factory=dict_row) as con:
        yield con
