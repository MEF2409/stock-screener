"""Fetch OHLCV data from yfinance and persist to SQLite."""

import time
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from typing import List

from .db import get_connection


# Yahoo throttles after a few hundred sequential yf.download calls. The
# first symptom is empty DataFrames returned silently — every ticker comes
# back as "possibly delisted; no price data found" until the limit clears.
# We retry with exponential backoff and treat empty as a transient error.
_MAX_ATTEMPTS = 4
_BASE_SLEEP = 1.5  # seconds; doubles each retry


def fetch_ohlcv(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    Fetch daily OHLCV data for a ticker from yfinance.

    Args:
        ticker: Stock ticker (e.g., 'AAPL')
        start_date: Start date as 'YYYY-MM-DD'
        end_date: End date as 'YYYY-MM-DD'

    Returns:
        DataFrame with columns: Open, High, Low, Close, Volume
    """
    last_err: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            df = yf.download(
                ticker, start=start_date, end=end_date,
                progress=False, auto_adjust=True, threads=False,
            )
        except Exception as exc:  # transient network / json / cookie failures
            last_err = exc
            df = pd.DataFrame()

        if not df.empty:
            # yfinance returns a MultiIndex on columns when given a single
            # ticker (e.g. ('Open', 'ABT')). Flatten by dropping the ticker
            # level so we can select by name. Positional rename is unsafe —
            # yfinance's column order changed to alphabetical (Close, High,
            # Low, Open, Volume), which silently swapped Open/Close.
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.reset_index()
            df = df[['Date', 'Open', 'High', 'Low', 'Close', 'Volume']]
            df['Date'] = pd.to_datetime(df['Date']).dt.strftime('%Y-%m-%d')
            return df

        # Empty result — back off and retry. Don't sleep after the last
        # attempt; just let the caller treat it as no-data.
        if attempt < _MAX_ATTEMPTS - 1:
            time.sleep(_BASE_SLEEP * (2 ** attempt))

    # Ran out of retries; return whatever yfinance gave us (empty df).
    return pd.DataFrame()


def store_ohlcv(ticker: str, df: pd.DataFrame) -> None:
    """
    Store OHLCV data in SQLite. Replaces existing data for the ticker/date pairs.

    Args:
        ticker: Stock ticker
        df: DataFrame with columns Date, Open, High, Low, Close, Volume
    """
    conn = get_connection()
    cursor = conn.cursor()

    for _, row in df.iterrows():
        cursor.execute("""
            INSERT OR REPLACE INTO daily_ohlcv
            (ticker, date, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            ticker,
            row['Date'],
            row['Open'],
            row['High'],
            row['Low'],
            row['Close'],
            int(row['Volume'])
        ))

    conn.commit()
    conn.close()


def get_ohlcv(ticker: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
    """
    Retrieve OHLCV data from SQLite for a ticker and optional date range.

    Args:
        ticker: Stock ticker
        start_date: Optional start date as 'YYYY-MM-DD'
        end_date: Optional end date as 'YYYY-MM-DD'

    Returns:
        DataFrame with columns: Date, Open, High, Low, Close, Volume
    """
    conn = get_connection()

    query = "SELECT date as Date, open as Open, high as High, low as Low, close as Close, volume as Volume FROM daily_ohlcv WHERE ticker = ?"
    params = [ticker]

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
    """Delete all OHLCV data for a ticker."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM daily_ohlcv WHERE ticker = ?", (ticker,))
    conn.commit()
    conn.close()
