#!/usr/bin/env python3
"""Test indicators: RSI, moving averages, avg volume."""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from stock_screener.data.db import init_db
from stock_screener.data.fetcher import fetch_ohlcv, store_ohlcv, get_ohlcv
from stock_screener.indicators.indicators import (
    enrich_ohlcv_with_indicators,
    get_52_week_high_low,
    get_previous_52_week_extremum,
)


def main():
    print("Stock Screener Indicators Test")
    print("=" * 50)

    # Initialize and fetch AAPL data
    print("\n1. Fetching AAPL data...")
    init_db()
    df = fetch_ohlcv("AAPL", "2025-04-26", "2026-04-26")
    store_ohlcv("AAPL", df)
    print(f"   Fetched: {len(df)} days")

    # Retrieve and enrich with indicators
    print("\n2. Calculating indicators...")
    df = get_ohlcv("AAPL")
    df = enrich_ohlcv_with_indicators(df)
    print(f"   Columns: {list(df.columns)}")

    # Show latest values
    print("\n3. Latest values (2026-04-24):")
    latest = df.iloc[-1]
    print(f"   Date: {latest['Date']}")
    print(f"   Close: ${latest['Close']:.2f}")
    print(f"   RSI(14): {latest['RSI_14']:.2f}")
    print(f"   MA(50): ${latest['MA_50']:.2f}")
    print(f"   MA(100): ${latest['MA_100']:.2f}")
    print(f"   MA(200): ${latest['MA_200']:.2f}")
    print(f"   Avg Vol(30d): {latest['Avg_Volume_30d']:,.0f}")

    # Get 52-week high/low
    print("\n4. 52-week high/low:")
    h, l, hd, ld, hr, lr = get_52_week_high_low(df, lookback_days=250)
    if h:
        print(f"   High: ${h:.2f} on {hd} (RSI: {hr:.2f})")
        print(f"   Low: ${l:.2f} on {ld} (RSI: {lr:.2f})")
    else:
        print("   (Insufficient data for 52-week window)")

    # Get previous 52-week high
    print("\n5. Previous 52-week high (before latest date):")
    prev_h, prev_hd, prev_hr = get_previous_52_week_extremum(df, df.iloc[-1]["Date"], is_high=True)
    if prev_h:
        print(f"   Previous High: ${prev_h:.2f} on {prev_hd} (RSI: {prev_hr:.2f})")
    else:
        print("   (No prior high found in window)")

    # Sanity checks
    print("\n6. Sanity checks:")
    print(f"   RSI in [0, 100]: {(df['RSI_14'].dropna() >= 0).all() and (df['RSI_14'].dropna() <= 100).all()}")
    print(f"   MA(50) < MA(100) < MA(200) in typical trend: {(df['MA_50'].iloc[-1] < df['MA_200'].iloc[-1])}")

    print("\n" + "=" * 50)
    print("✓ Indicators test complete!")


if __name__ == "__main__":
    main()
