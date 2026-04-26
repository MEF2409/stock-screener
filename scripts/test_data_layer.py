#!/usr/bin/env python3
"""Test script: fetch 1yr AAPL data and store in SQLite."""

import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from stock_screener.data.db import init_db, get_db_path
from stock_screener.data.fetcher import fetch_ohlcv, store_ohlcv, get_ohlcv


def main():
    print("Stock Screener Data Layer Test")
    print("=" * 50)

    # 1. Initialize database
    print("\n1. Initializing database...")
    init_db()
    db_path = get_db_path()
    print(f"   Database: {db_path}")
    print(f"   Exists: {db_path.exists()}")

    # 2. Fetch 1 year of AAPL data
    print("\n2. Fetching 1 year of AAPL data from yfinance...")
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    print(f"   Date range: {start_date} to {end_date}")

    df = fetch_ohlcv("AAPL", start_date, end_date)
    print(f"   Rows fetched: {len(df)}")
    print(f"\n   First 3 rows:")
    print(df.head(3).to_string(index=False))
    print(f"\n   Last 3 rows:")
    print(df.tail(3).to_string(index=False))

    # 3. Store in SQLite
    print("\n3. Storing AAPL data in SQLite...")
    store_ohlcv("AAPL", df)
    print("   Stored!")

    # 4. Retrieve and verify
    print("\n4. Retrieving from SQLite and verifying...")
    df_retrieved = get_ohlcv("AAPL")
    print(f"   Rows retrieved: {len(df_retrieved)}")
    print(f"   Match: {len(df_retrieved) == len(df)}")

    print(f"\n   First 3 rows from DB:")
    print(df_retrieved.head(3).to_string(index=False))
    print(f"\n   Last 3 rows from DB:")
    print(df_retrieved.tail(3).to_string(index=False))

    # 5. Verify price data makes sense
    print("\n5. Quick sanity checks:")
    latest = df_retrieved.iloc[-1]
    print(f"   Latest close: ${latest['Close']:.2f}")
    print(f"   Latest volume: {latest['Volume']:,}")
    print(f"   High > Low for all rows: {(df_retrieved['High'] >= df_retrieved['Low']).all()}")
    print(f"   Close between Low and High: {((df_retrieved['Close'] >= df_retrieved['Low']) & (df_retrieved['Close'] <= df_retrieved['High'])).all()}")

    print("\n" + "=" * 50)
    print("✓ Data layer test complete!")


if __name__ == "__main__":
    main()
