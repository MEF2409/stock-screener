#!/usr/bin/env python3
"""Test earnings calendar: fetch and filter by 14-day window."""

import sys
from pathlib import Path
from datetime import datetime, timedelta

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from stock_screener.data.db import init_db, get_connection
from stock_screener.earnings.earnings import has_earnings_within_days, get_earnings_dates


def main():
    print("Stock Screener Earnings Calendar Test")
    print("=" * 50)

    # Initialize database
    print("\n1. Initializing database...")
    init_db()
    print("   ✓ Done")

    # Insert test earnings data manually (yfinance.Ticker.info is too slow for testing)
    print("\n2. Inserting test earnings data...")
    conn = get_connection()
    cursor = conn.cursor()

    test_data = [
        ("AAPL", None),  # No earnings (safe)
        ("MSFT", (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")),  # Within 14d
        ("GOOGL", (datetime.now() + timedelta(days=20)).strftime("%Y-%m-%d")),  # Outside 14d
        ("AMZN", (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")),  # Within 14d
        ("TSLA", (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")),  # In past (reported)
    ]

    for ticker, earnings_date in test_data:
        cursor.execute("""
            INSERT OR REPLACE INTO earnings (ticker, next_earnings_date, last_updated)
            VALUES (?, ?, ?)
        """, (ticker, earnings_date, datetime.now().isoformat()))

    conn.commit()
    conn.close()

    # Retrieve and display
    print("\n3. Earnings dates:")
    earnings_df = get_earnings_dates()
    if len(earnings_df) > 0:
        print(earnings_df.to_string(index=False))
    else:
        print("   (No earnings data)")

    # Test 14-day filter
    print("\n4. Testing 14-day earnings filter:")
    for ticker, _ in test_data:
        has_earnings = has_earnings_within_days(ticker, days=14)
        status = "⚠️  Earnings within 14d (skip)" if has_earnings else "✓ OK (no earnings within 14d)"
        print(f"   {ticker}: {status}")

    print("\n" + "=" * 50)
    print("✓ Earnings calendar test complete!")
    print("\nNote: In production, populate earnings with update_earnings_calendar().")
    print("yfinance.Ticker.info is slow (~1-2s per ticker); consider Finnhub API for speed.")


if __name__ == "__main__":
    main()

