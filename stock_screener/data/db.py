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
    db_path.parent.mkdir(parents=True, exist_ok=True)

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
            avg_volume_30d INTEGER NOT NULL
        )
    """)

    # Earnings dates table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS earnings (
            ticker TEXT PRIMARY KEY,
            next_earnings_date TEXT,
            last_updated TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def get_connection() -> sqlite3.Connection:
    """Return a SQLite connection with row factory for dict-like access."""
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    return conn
