"""Fetch OHLCV data via the active MarketDataProvider and persist to SQLite.

The public functions (fetch_ohlcv, fetch_ohlcv_bulk, store_ohlcv,
get_ohlcv, delete_ohlcv) preserve the historical signatures. Under
the hood, fetch_* delegates to whichever provider DATA_PROVIDER
selects (yfinance | massive | bloomberg). Everything else in the
codebase — scanners, dashboard, cron scripts — was calling these
functions and continues to work unchanged.
"""

from __future__ import annotations

import pandas as pd

from .db import get_connection
from .providers import get_provider


def fetch_ohlcv(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Daily OHLCV for a single ticker between start_date (inclusive)
    and end_date (exclusive). Both dates are 'YYYY-MM-DD'. Returns a
    DataFrame with columns Date, Open, High, Low, Close, Volume; empty
    DataFrame when no data is available.
    """
    return get_provider().fetch_ohlcv(ticker, start_date, end_date)


def fetch_ohlcv_bulk(
    tickers: list[str],
    start_date: str,
    end_date: str,
    chunk_size: int = 200,   # accepted for backward compat; provider-specific
) -> dict[str, pd.DataFrame]:
    """Daily OHLCV for many tickers at once. Returns {ticker: DataFrame}
    — tickers with no data are omitted.

    chunk_size is preserved for the yfinance provider (still used to
    chunk yf.download calls). Massive uses grouped-daily fanout and
    ignores it.
    """
    return get_provider().fetch_ohlcv_bulk(tickers, start_date, end_date)


def store_ohlcv(ticker: str, df: pd.DataFrame) -> None:
    """Upsert OHLCV rows into SQLite for the given ticker."""
    conn = get_connection()
    cursor = conn.cursor()

    for _, row in df.iterrows():
        cursor.execute(
            """
            INSERT OR REPLACE INTO daily_ohlcv
            (ticker, date, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticker,
                row["Date"],
                row["Open"],
                row["High"],
                row["Low"],
                row["Close"],
                int(row["Volume"]),
            ),
        )

    conn.commit()
    conn.close()


def get_ohlcv(
    ticker: str, start_date: str | None = None, end_date: str | None = None
) -> pd.DataFrame:
    """Read OHLCV back from SQLite for a ticker + optional date range."""
    conn = get_connection()

    query = (
        "SELECT date as Date, open as Open, high as High, low as Low, "
        "close as Close, volume as Volume FROM daily_ohlcv WHERE ticker = ?"
    )
    params: list = [ticker]
    if start_date:
        query += " AND date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND date <= ?"
        params.append(end_date)
    query += " ORDER BY date ASC"

    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df


def delete_ohlcv(ticker: str) -> None:
    """Delete all OHLCV rows for a ticker."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM daily_ohlcv WHERE ticker = ?", (ticker,))
    conn.commit()
    conn.close()
