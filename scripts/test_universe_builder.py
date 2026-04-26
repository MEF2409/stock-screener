#!/usr/bin/env python3
"""Test universe builder: qualify stocks, update universe."""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from stock_screener.data.db import init_db
from stock_screener.universe.builder import get_qualified_stocks, update_universe, get_universe


def main():
    print("Stock Screener Universe Builder Test")
    print("=" * 50)

    # Initialize database
    print("\n1. Initializing database...")
    init_db()
    print("   ✓ Done")

    # Get qualified stocks
    print("\n2. Fetching and qualifying stocks...")
    df = get_qualified_stocks(min_price=5.0, min_avg_volume=500000)
    print(f"   Qualified stocks: {len(df)}")
    if len(df) > 0:
        print("\n   Sample (first 5):")
        print(df.head(5).to_string(index=False))

    # Update universe in SQLite
    print("\n3. Updating universe in SQLite...")
    update_universe(min_price=5.0, min_avg_volume=500000)

    # Retrieve and display
    print("\n4. Retrieving universe from SQLite...")
    universe = get_universe()
    print(f"   Stored: {len(universe)} stocks")
    if len(universe) > 0:
        print("\n   Sample (first 5):")
        print(universe.head(5).to_string(index=False))
        print(f"\n   Summary:")
        print(f"   - Min price: ${universe['price'].min():.2f}")
        print(f"   - Max price: ${universe['price'].max():.2f}")
        print(f"   - Avg 30d volume: {universe['avg_volume_30d'].mean():,.0f}")

    print("\n" + "=" * 50)
    print("✓ Universe builder test complete!")


if __name__ == "__main__":
    main()
