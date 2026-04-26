#!/usr/bin/env python3
"""Test bulk refresh: fetch 1yr for all universe stocks."""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from stock_screener.data.db import init_db
from stock_screener.data.bulk_refresh import refresh_all_ohlcv
from stock_screener.universe.builder import get_universe, update_universe


def main():
    print("Stock Screener Bulk Refresh Test")
    print("=" * 50)

    # Initialize database
    print("\n1. Initializing database...")
    init_db()
    print("   ✓ Done")

    # Build universe
    print("\n2. Building universe...")
    update_universe()

    universe = get_universe()
    print(f"   Universe: {len(universe)} stocks")

    # Refresh OHLCV for all stocks
    print("\n3. Refreshing OHLCV data for all universe stocks...")
    refresh_all_ohlcv(days_back=365)

    print("\n" + "=" * 50)
    print("✓ Bulk refresh test complete!")


if __name__ == "__main__":
    main()
