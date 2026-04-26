"""Fetch and manage earnings calendar for stocks."""

import yfinance as yf
from datetime import datetime, timedelta
import pandas as pd

from stock_screener.data.db import get_connection


def fetch_next_earnings_date(ticker: str) -> str:
    """
    Fetch the next earnings announcement date for a stock.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Earnings date as 'YYYY-MM-DD' string, or None if not available

    Note: yfinance.Ticker.info is slow (~1-2 seconds per ticker). For production,
    consider using Finnhub API or another data source with bulk earnings endpoints.
    """
    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        # yfinance provides earnings date as a Unix timestamp
        if "earningsDate" in info and info["earningsDate"]:
            # earningsDate is a list of [start, end] timestamps
            earnings_ts = info["earningsDate"][0]
            earnings_date = datetime.fromtimestamp(earnings_ts).strftime("%Y-%m-%d")
            return earnings_date
        else:
            return None
    except Exception as e:
        return None


def update_earnings_calendar(tickers: list) -> None:
    """
    Fetch and update earnings dates for a list of tickers in SQLite.

    Args:
        tickers: List of ticker symbols
    """
    conn = get_connection()
    cursor = conn.cursor()

    print(f"Updating earnings calendar for {len(tickers)} stocks...")

    success_count = 0
    for i, ticker in enumerate(tickers):
        if (i + 1) % 10 == 0:
            print(f"  Progress: {i + 1}/{len(tickers)}")

        try:
            earnings_date = fetch_next_earnings_date(ticker)

            cursor.execute("""
                INSERT OR REPLACE INTO earnings (ticker, next_earnings_date, last_updated)
                VALUES (?, ?, ?)
            """, (ticker, earnings_date, datetime.now().isoformat()))

            success_count += 1
        except Exception as e:
            # Silently continue if fetch fails
            continue

    conn.commit()
    conn.close()

    print(f"Earnings calendar updated: {success_count} stocks")


def has_earnings_within_days(ticker: str, days: int = 14) -> bool:
    """
    Check if a stock has an earnings announcement within the next N days.

    Args:
        ticker: Stock ticker
        days: Number of days to look ahead (default 14)

    Returns:
        True if earnings within the window, False otherwise
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT next_earnings_date FROM earnings WHERE ticker = ?
    """, (ticker,))

    row = cursor.fetchone()
    conn.close()

    if not row or not row[0]:
        # No earnings date recorded; assume it's safe (not blocking)
        return False

    earnings_date = datetime.strptime(row[0], "%Y-%m-%d").date()
    today = datetime.now().date()
    days_until_earnings = (earnings_date - today).days

    return 0 <= days_until_earnings <= days


def get_earnings_dates(tickers: list = None) -> pd.DataFrame:
    """
    Retrieve earnings dates for tickers from SQLite.

    Args:
        tickers: Optional list of tickers to filter. If None, returns all.

    Returns:
        DataFrame with columns: ticker, next_earnings_date, last_updated
    """
    conn = get_connection()

    if tickers:
        placeholders = ",".join(["?"] * len(tickers))
        query = f"SELECT * FROM earnings WHERE ticker IN ({placeholders}) ORDER BY ticker"
        df = pd.read_sql_query(query, conn, params=tickers)
    else:
        df = pd.read_sql_query("SELECT * FROM earnings ORDER BY ticker", conn)

    conn.close()
    return df
