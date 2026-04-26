#!/usr/bin/env python3
"""Test all four scanners."""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from stock_screener.data.db import init_db
from stock_screener.data.bulk_refresh import refresh_all_ohlcv
from stock_screener.universe.builder import get_universe, update_universe
from stock_screener.scanners.scanners import run_all_scanners


def main():
    print("Stock Screener Scanners Test")
    print("=" * 50)

    # Setup
    print("\n1. Initializing and building universe...")
    init_db()
    update_universe()
    universe = get_universe()
    print(f"   Universe: {len(universe)} stocks")

    # Refresh data
    print("\n2. Refreshing OHLCV data for universe...")
    refresh_all_ohlcv(days_back=365)

    # Run scanners
    print("\n3. Running scanners on universe...")
    tickers = universe["ticker"].tolist()
    results = run_all_scanners(tickers)

    # Display results
    print("\n" + "=" * 50)
    print("SCANNER RESULTS")
    print("=" * 50)

    for scanner_name, flags in results.items():
        display_name = {
            "runaway_gap": "Bull #1 — Runaway Gap",
            "bullish_div": "Bull #2 — Bullish Divergence",
            "bearish_div": "Bear #1 — Bearish Divergence",
            "gap_up_normal_vol": "Bear #2 — Gap Up Normal Volume",
        }[scanner_name]

        print(f"\n{display_name}: {len(flags)} flags")
        for flag in flags[:3]:  # Show first 3
            print(f"  ✓ {flag['ticker']} — {flag['reason']}")
        if len(flags) > 3:
            print(f"  ... and {len(flags) - 3} more")

    print("\n" + "=" * 50)
    total_flags = sum(len(flags) for flags in results.values())
    print(f"Total flags across all scanners: {total_flags}")
    print("✓ Scanners test complete!")


if __name__ == "__main__":
    main()
