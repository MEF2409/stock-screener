"""Fetch OHLCV data from yfinance and persist to SQLite."""

import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from typing import List

from .db import get_connection


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
    df = yf.download(ticker, start=start_date, end=end_date, progress=False)
    # yfinance returns dates as index; reset to column for consistency
    df.reset_index(inplace=True)
    df.columns = ['Date', 'Open', 'High', 'Low', 'Close', 'Volume']
    df['Date'] = df['Date'].dt.strftime('%Y-%m-%d')
    return df


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
