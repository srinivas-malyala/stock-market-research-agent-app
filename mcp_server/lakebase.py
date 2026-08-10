"""Lakebase access and migration helpers for the stock research app."""
from __future__ import annotations

import base64
import os
from contextlib import contextmanager
from pathlib import Path

import psycopg2
from databricks.sdk import WorkspaceClient
from psycopg2.extras import RealDictCursor


def get_lakebase_url() -> str:
    if os.getenv("LAKEBASE_URL"):
        return os.environ["LAKEBASE_URL"]
    secret = WorkspaceClient().secrets.get_secret(
        scope=os.getenv("LAKEBASE_SECRET_SCOPE", "database"),
        key=os.getenv("LAKEBASE_SECRET_KEY", "lakebase-url"),
    )
    return base64.b64decode(secret.value).decode("utf-8")


@contextmanager
def get_connection():
    connection = psycopg2.connect(get_lakebase_url(), cursor_factory=RealDictCursor)
    try:
        yield connection
    finally:
        connection.close()


def query(sql: str, params=None) -> list[dict]:
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]


def write(sql: str, params=None, returning: bool = False):
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(sql, params)
        value = dict(cursor.fetchone()) if returning else cursor.rowcount
        connection.commit()
        return value


def migrate() -> None:
    sql = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute(sql)
        connection.commit()

