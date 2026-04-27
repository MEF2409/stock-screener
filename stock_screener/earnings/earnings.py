"""Fetch and manage earnings calendar for stocks.

Primary source: Finnhub /calendar/earnings (bulk, fast).
Fallback: yfinance.Ticker.info (per-ticker, slow) when no FINNHUB_API_KEY is set
or the Finnhub call fails.
"""

import os
from datetime import datetime, timedelta

import pandas as pd
import requests
import yfinance as yf

from stock_screener.data.db import get_connection

FINNHUB_BASE = "https://finnhub.io/api/v1"
FINNHUB_KEY = os.getenv("FINNHUB_API_KEY")


def _fetch_finnhub_earnings_window(start: str, end: str) -> dict[str, str]:
    """Fetch earnings calendar from Finnhub for a date window.
    Returns {ticker: 'YYYY-MM-DD'} for the SOONEST date per ticker."""
    if not FINNHUB_KEY:
        return {}
    try:
        resp = requests.get(
            f"{FINNHUB_BASE}/calendar/earnings",
            params={"from": start, "to": end, "token": FINNHUB_KEY},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("earningsCalendar", []) or []
    except Exception:
        return {}

    out: dict[str, str] = {}
    for row in data:
        sym = row.get("symbol")
        date = row.get("date")
        if not sym or not date:
            continue
        # Keep earliest date if a ticker reports multiple times in the window
        if sym not in out or date < out[sym]:
            out[sym] = date
    return out


def fetch_next_earnings_date(ticker: str) -> str | None:
    """Fetch the next earnings date for a stock (single ticker, slow yfinance fallback)."""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        if "earningsDate" in info and info["earningsDate"]:
            earnings_ts = info["earningsDate"][0]
            return datetime.fromtimestamp(earnings_ts).strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


def update_earnings_calendar(tickers: list, lookahead_days: int = 60) -> None:
    """
    Fetch and update earnings dates for a list of tickers in SQLite.

    Uses Finnhub bulk endpoint when FINNHUB_API_KEY is set; falls back to
    per-ticker yfinance for any tickers Finnhub didn't return.
    """
    conn = get_connection()
    cursor = conn.cursor()

    today = datetime.now().date()
    start = today.isoformat()
    end = (today + timedelta(days=lookahead_days)).isoformat()

    finnhub_dates: dict[str, str] = {}
    if FINNHUB_KEY:
        print(f"Fetching earnings from Finnhub: {start} to {end}")
        finnhub_dates = _fetch_finnhub_earnings_window(start, end)
        print(f"  Finnhub returned {len(finnhub_dates)} ticker-dates")
    else:
        print("FINNHUB_API_KEY not set — falling back to yfinance (slow).")

    print(f"Updating earnings calendar for {len(tickers)} stocks...")

    now = datetime.now().isoformat()
    success_count = 0
    fallback_count = 0
    for i, ticker in enumerate(tickers):
        if (i + 1) % 25 == 0:
            print(f"  Progress: {i + 1}/{len(tickers)}")

        earnings_date = finnhub_dates.get(ticker)
        if not earnings_date:
            # Finnhub didn't have it (or no key) — try yfinance
            earnings_date = fetch_next_earnings_date(ticker)
            if earnings_date:
                fallback_count += 1

        try:
            cursor.execute(
                "INSERT OR REPLACE INTO earnings (ticker, next_earnings_date, last_updated) VALUES (?, ?, ?)",
                (ticker, earnings_date, now),
            )
            success_count += 1
        except Exception:
            continue

    conn.commit()
    conn.close()
    print(f"Earnings calendar updated: {success_count} stocks ({fallback_count} via yfinance fallback)")


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
