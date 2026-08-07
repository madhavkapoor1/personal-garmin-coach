"""SQLite access: schema init, connections, and generic UPSERT helpers.

Two connection flavors:
  * connect()    - read/write, used by ingestion.
  * connect_ro() - read-only (immutable URI), used by the dashboard and the
                   MCP server so a bug or an over-eager model can never mutate
                   your history.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import config


def connect(db_path: Path | str = None) -> sqlite3.Connection:
    """Read/write connection. Ensures the data dir exists."""
    config.ensure_dirs()
    path = Path(db_path) if db_path else config.DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def connect_ro(db_path: Path | str = None) -> sqlite3.Connection:
    """Read-only connection via SQLite URI. Raises if the DB doesn't exist."""
    path = Path(db_path) if db_path else config.DB_PATH
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# New columns added to existing tables after v1. Applied idempotently on every
# init so an existing garmin.db evolves without a manual migration.
_MIGRATIONS = [
    ("activities", "temp_c", "REAL"),
    ("activities", "feels_like_c", "REAL"),
    ("activities", "humidity", "REAL"),
    ("activities", "weather_desc", "TEXT"),
    ("daily_stats", "spo2_avg", "REAL"),
    ("daily_stats", "resp_waking", "REAL"),
    ("daily_stats", "resp_sleep", "REAL"),
    ("daily_stats", "hydration_ml", "REAL"),
    ("daily_stats", "hydration_goal_ml", "REAL"),
    ("fitness", "ftp_watts", "REAL"),
    ("fitness", "ftp_w_per_kg", "REAL"),
]


def init_db(db_path: Path | str = None) -> None:
    """Create tables/indexes from schema.sql, then apply column migrations."""
    sql = config.SCHEMA_PATH.read_text(encoding="utf-8")
    with connect(db_path) as conn:
        conn.executescript(sql)
        for table, col, coltype in _MIGRATIONS:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.commit()


def upsert(conn: sqlite3.Connection, table: str, row: Mapping, pk: Sequence[str]) -> None:
    """INSERT ... ON CONFLICT(pk) DO UPDATE for a single dict row.

    Only non-None columns are written on update, so a partial re-pull never
    overwrites good data with NULLs. Unknown/None-only rows are skipped.
    """
    cols = [k for k, v in row.items() if v is not None]
    if not cols:
        return
    placeholders = ", ".join("?" for _ in cols)
    col_list = ", ".join(cols)
    pk_list = ", ".join(pk)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in pk)
    if updates:
        conflict = f"ON CONFLICT({pk_list}) DO UPDATE SET {updates}"
    else:
        conflict = f"ON CONFLICT({pk_list}) DO NOTHING"
    conn.execute(
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) {conflict}",
        [row[c] for c in cols],
    )


def upsert_many(
    conn: sqlite3.Connection, table: str, rows: Iterable[Mapping], pk: Sequence[str]
) -> int:
    n = 0
    for r in rows:
        upsert(conn, table, r, pk)
        n += 1
    return n


def log_ingest(
    conn: sqlite3.Connection, date: str, dataset: str, status: str, msg: str = ""
) -> None:
    """Record per (date, dataset) ingest outcome for resumable backfill."""
    upsert(
        conn,
        "ingest_log",
        {
            "date": date,
            "dataset": dataset,
            "status": status,
            "msg": msg[:500] if msg else "",
            "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
        },
        pk=["date", "dataset"],
    )


def date_done(conn: sqlite3.Connection, date: str, dataset: str) -> bool:
    """True if this (date, dataset) already completed OK (skip during backfill)."""
    cur = conn.execute(
        "SELECT status FROM ingest_log WHERE date=? AND dataset=?", (date, dataset)
    )
    row = cur.fetchone()
    return bool(row and row["status"] == "ok")
