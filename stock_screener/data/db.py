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
    # Additive: last_earnings_date so we can tag signals whose gap is the
    # post-print reaction (catalyst='earnings' vs 'none').
    cursor.execute("PRAGMA table_info(earnings)")
    earnings_cols = {row[1] for row in cursor.fetchall()}
    if "last_earnings_date" not in earnings_cols:
        cursor.execute("ALTER TABLE earnings ADD COLUMN last_earnings_date TEXT")

    # Per-ticker historical earnings-reaction stats. Pre-computed nightly so
    # the dashboard can show "this stock's last N earnings: avg gap ±X%,
    # fade-back rate Y%" next to ⚡ EARNINGS signals without per-render API
    # calls.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS earnings_reactions (
            ticker TEXT PRIMARY KEY,
            n_events INTEGER NOT NULL,
            avg_gap_pct REAL,
            avg_same_day_pct REAL,
            avg_5d_pct REAL,
            fade_rate REAL,
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
    # Additive: JSON blob for user preferences (account size, risk %, alert
    # opt-ins, etc). Storing as JSON text keeps the schema flexible.
    cursor.execute("PRAGMA table_info(users)")
    user_cols = {row[1] for row in cursor.fetchall()}
    if "prefs" not in user_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN prefs TEXT NOT NULL DEFAULT '{}'")

    # Trades — manual journal of executed positions for tracking + grading.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner TEXT NOT NULL,
            ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            entry_price REAL NOT NULL,
            shares INTEGER NOT NULL,
            exit_date TEXT,
            exit_price REAL,
            notes TEXT,
            created_at TEXT NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_owner ON trades(owner)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker)")
    # Additive migration: setup tag drives which exit playbook applies.
    cursor.execute("PRAGMA table_info(trades)")
    trade_cols = {row[1] for row in cursor.fetchall()}
    if "setup" not in trade_cols:
        cursor.execute("ALTER TABLE trades ADD COLUMN setup TEXT NOT NULL DEFAULT 'manual'")

    # Job runs — one row per script execution (cron or manual). Powers the
    # 'Data Health' dashboard panel. status is 'running' | 'success' |
    # 'failed'; message holds the error trace on failure.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS job_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_name TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            triggered_by TEXT NOT NULL DEFAULT 'cron',
            message TEXT,
            log_path TEXT,
            pid INTEGER
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_job_runs_job_started ON job_runs(job_name, started_at DESC)")

    conn.commit()
    conn.close()


def get_connection() -> sqlite3.Connection:
    """Return a SQLite connection with row factory for dict-like access."""
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    return conn
