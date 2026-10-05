"""Database connection and reset.

One connection string, read from the environment so the same code runs in
WSL, in Docker Compose and in CI without edits.
"""
from __future__ import annotations

import os
import pathlib

import psycopg

DSN = os.environ.get(
    "OUTBOX_DSN",
    "host=localhost port=5432 dbname=outboxlab user=postgres password=postgres",
)

ROOT = pathlib.Path(__file__).resolve().parent.parent


def connect() -> psycopg.Connection:
    """A connection with autocommit OFF, because this lab is about transactions."""
    return psycopg.connect(DSN, autocommit=False)


def reset_schema() -> None:
    """Drop and recreate everything. Every scenario starts from nothing."""
    sql = (ROOT / "sql" / "schema.sql").read_text(encoding="utf-8")
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql)


def counts(conn: psycopg.Connection) -> dict[str, int]:
    """Row counts for the invariant checks and the report."""
    out = {}
    for table in ("orders", "outbox", "emails_sent", "processed_events",
                  "dead_letters"):
        out[table] = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    out["outbox_unpublished"] = conn.execute(
        "SELECT count(*) FROM outbox WHERE published_at IS NULL").fetchone()[0]
    return out
