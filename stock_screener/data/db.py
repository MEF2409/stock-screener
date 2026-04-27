"""SQLite database schema and connection management."""

import sqlite3
import os
from pathlib import Path
from datetime import datetime
from typing import Optional


DB_PATH = Path(__file__).parent.parent.parent / "db" / "screener.db"


def get_db_path() -> Path:
    """Return the database file path."""
    return DB_PATH


def init_db() -> None:
    """Initialize database schema if it doesn't exist."""
    db_path = get_db_path()
    # If db_path.parent is a symlink (containers do this for volume mounts),
    # mkdir on the symlink fails when the target doesn't exist. Resolve to the
    # real target and mkdir that instead.
    parent = db_path.parent
    target = parent.resolve() if parent.is_symlink() else parent
    os.makedirs(target, exist_ok=True)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Price/volume data table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_ohlcv (
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume INTEGER NOT NULL,
            PRIMARY KEY (ticker, date)
        )
    """)

    # Universe (qualified stocks) table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS universe (
            ticker TEXT PRIMARY KEY,
            last_updated TEXT NOT NULL,
            price REAL NOT NULL,
            avg_volume_30d INTEGER NOT NULL,
            sector TEXT,
            company_name TEXT
        )
    """)
    # Add columns if they don't exist (idempotent migration)
    cursor.execute("PRAGMA table_info(universe)")
    cols = {row[1] for row in cursor.fetchall()}
    if "sector" not in cols:
        cursor.execute("ALTER TABLE universe ADD COLUMN sector TEXT")
    if "company_name" not in cols:
        cursor.execute("ALTER TABLE universe ADD COLUMN company_name TEXT")

    # Earnings dates table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS earnings (
            ticker TEXT PRIMARY KEY,
            next_earnings_date TEXT,
            last_updated TEXT NOT NULL
        )
    """)

    # Daily snapshot of all flagged signals (for historical density / trends)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scan_history (
            run_date TEXT NOT NULL,
            scanner TEXT NOT NULL,
            ticker TEXT NOT NULL,
            PRIMARY KEY (run_date, scanner, ticker)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_scan_history_date ON scan_history(run_date)")

    # Users table — accounts that can log in. status: pending/approved/rejected.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            email TEXT,
            name TEXT,
            password_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            approved_at TEXT
        )
    """)

    conn.commit()
    conn.close()


def get_connection() -> sqlite3.Connection:
    """Return a SQLite connection with row factory for dict-like access."""
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    return conn
