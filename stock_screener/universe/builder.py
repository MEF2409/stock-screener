"""Build and manage the universe of stocks eligible for screening."""

import yfinance as yf
import pandas as pd
from datetime import datetime
from typing import List

from stock_screener.data.db import get_connection


def get_all_nyse_nasdaq_tickers() -> List[str]:
    """
    Fetch all NYSE and NASDAQ listed common stock tickers.

    Uses yfinance's built-in ticker list. For production, consider caching
    this as it can take a minute to fetch.

    Returns:
        List of ticker symbols
    """
    print("Fetching NYSE tickers...")
    nyse_data = yf.download("^IXIC", progress=False)  # Placeholder; we'll use a different approach

    # For now, we'll use a minimal approach: yfinance doesn't have a built-in
    # comprehensive ticker list, so we'll start with a known list and expand later.
    # In production, use:
    # - https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company_type=inc&CIK=...
    # - https://www.nasdaq.com/market-activity/stocks
    # - A CSV file from an external source

    # Placeholder: return a small list for testing
    return [
        "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "GOOG",
        "BRK.B", "JNJ", "V", "WMT", "JPM", "MA", "PG", "UNH", "COST", "HD",
        "CMG", "NFLX", "AMD", "INTC", "CRM", "ADBE", "MU", "CSCO", "PYPL",
        "F", "GE", "GM", "IBM", "BA", "PLTR", "SQ", "DKNG", "COIN", "RKT"
    ]


def get_qualified_stocks(min_price: float = 5.0, min_avg_volume: int = 500000) -> pd.DataFrame:
    """
    Filter stocks from NYSE/NASDAQ that meet price and volume criteria.

    Args:
        min_price: Minimum stock price (default $5)
        min_avg_volume: Minimum 30-day average daily volume (default 500k)

    Returns:
        DataFrame with columns: ticker, price, avg_volume_30d, timestamp
    """
    tickers = get_all_nyse_nasdaq_tickers()
    qualified = []

    print(f"Screening {len(tickers)} tickers...")
    for i, ticker in enumerate(tickers):
        if (i + 1) % 10 == 0:
            print(f"  Progress: {i + 1}/{len(tickers)}")

        try:
            # Fetch latest data (use a small window to get recent prices)
            data = yf.download(ticker, period="60d", progress=False)

            if data.empty or len(data) < 30:
                continue

            # Handle multi-index columns (if fetching multiple tickers at once)
            if isinstance(data.columns, pd.MultiIndex):
                close_col = data["Close"][ticker]
                volume_col = data["Volume"][ticker]
            else:
                close_col = data["Close"]
                volume_col = data["Volume"]

            latest_close = float(close_col.iloc[-1])
            avg_volume_30d = int(volume_col.tail(30).mean())

            if latest_close >= min_price and avg_volume_30d >= min_avg_volume:
                qualified.append({
                    "ticker": ticker,
                    "price": latest_close,
                    "avg_volume_30d": avg_volume_30d,
                    "timestamp": datetime.now().isoformat()
                })

        except Exception as e:
            # Silently skip tickers that fail (delisted, invalid, etc.)
            continue

    df = pd.DataFrame(qualified)
    print(f"Qualified: {len(df)} stocks")
    return df


def update_universe(min_price: float = 5.0, min_avg_volume: int = 500000) -> None:
    """
    Recompute and store the universe of qualified stocks in SQLite.

    Args:
        min_price: Minimum stock price
        min_avg_volume: Minimum 30-day average daily volume
    """
    df = get_qualified_stocks(min_price, min_avg_volume)

    conn = get_connection()
    cursor = conn.cursor()

    # Clear old universe
    cursor.execute("DELETE FROM universe")

    # Insert new universe
    for _, row in df.iterrows():
        cursor.execute("""
            INSERT INTO universe (ticker, last_updated, price, avg_volume_30d)
            VALUES (?, ?, ?, ?)
        """, (
            row["ticker"],
            row["timestamp"],
            row["price"],
            row["avg_volume_30d"]
        ))

    conn.commit()
    conn.close()

    print(f"Universe updated: {len(df)} stocks stored in SQLite")


def get_universe() -> pd.DataFrame:
    """Retrieve the current universe of qualified stocks from SQLite."""
    conn = get_connection()
    df = pd.read_sql_query("SELECT * FROM universe ORDER BY ticker ASC", conn)
    conn.close()
    return df
