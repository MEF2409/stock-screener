"""Bulk fetch and refresh OHLCV data for the universe."""

from datetime import datetime, timedelta
import pandas as pd

from stock_screener.data.fetcher import fetch_ohlcv, store_ohlcv
from stock_screener.universe.builder import get_universe


def refresh_all_ohlcv(days_back: int = 365) -> None:
    """
    Fetch and refresh 1 year (or specified days) of OHLCV data for all stocks in the universe.

    Args:
        days_back: Number of trading days to fetch (default 365 ≈ 1 year)
    """
    universe = get_universe()

    if len(universe) == 0:
        print("Universe is empty. Update universe first with update_universe().")
        return

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    print(f"Refreshing OHLCV data for {len(universe)} stocks")
    print(f"Date range: {start_date} to {end_date}")

    failed = []
    success_count = 0

    for i, row in enumerate(universe.iterrows()):
        ticker = row[1]["ticker"]

        if (i + 1) % 10 == 0:
            print(f"  Progress: {i + 1}/{len(universe)} (success: {success_count})")

        try:
            df = fetch_ohlcv(ticker, start_date, end_date)
            if len(df) > 0:
                store_ohlcv(ticker, df)
                success_count += 1
            else:
                failed.append((ticker, "No data returned"))
        except Exception as e:
            failed.append((ticker, str(e)))

    print(f"\nRefresh complete!")
    print(f"  Successful: {success_count}/{len(universe)}")
    if len(failed) > 0:
        print(f"  Failed: {len(failed)}")
        for ticker, reason in failed[:5]:
            print(f"    - {ticker}: {reason}")
        if len(failed) > 5:
            print(f"    ... and {len(failed) - 5} more")
