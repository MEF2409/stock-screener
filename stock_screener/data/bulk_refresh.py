"""Bulk fetch and refresh OHLCV data for the universe."""

import time
from datetime import datetime, timedelta
import pandas as pd

from stock_screener.data.fetcher import fetch_ohlcv, store_ohlcv
from stock_screener.universe.builder import get_universe


# When Yahoo rate-limits, every subsequent yf.download returns empty.
# Treat a streak of empties as a rate-limit signal and pause to let it
# clear instead of burning through 1500 more tickers with empty results.
_EMPTY_STREAK_TRIP = 15      # consecutive empties before we declare rate-limit
_RATE_LIMIT_COOLDOWN = 90    # seconds to wait when tripped
_RATE_LIMIT_MAX_TRIPS = 4    # give up after this many cooldowns in a single run
_INTER_TICKER_DELAY = 0.05   # small breather to flatten request rate


def refresh_all_ohlcv(days_back: int = 365, progress_callback=None) -> None:
    """
    Fetch and refresh OHLCV data for all stocks in the universe.

    Args:
        days_back: Number of trading days to fetch (default 365 ≈ 1 year)
        progress_callback: Optional fn(done:int, total:int, ticker:str, status:str)
            called after each ticker. status is "ok"/"empty"/"error".
    """
    universe = get_universe()

    if len(universe) == 0:
        print("Universe is empty. Update universe first with update_universe().")
        return

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    print(f"Refreshing OHLCV data for {len(universe)} stocks")
    print(f"Date range: {start_date} to {end_date}")

    total = len(universe)
    failed = []
    success_count = 0
    empty_streak = 0
    trips = 0

    for i, row in enumerate(universe.iterrows()):
        ticker = row[1]["ticker"]

        if (i + 1) % 50 == 0:
            print(f"  Progress: {i + 1}/{total} (success: {success_count})")

        status = "ok"
        try:
            df = fetch_ohlcv(ticker, start_date, end_date)
            if len(df) > 0:
                store_ohlcv(ticker, df)
                success_count += 1
                empty_streak = 0
            else:
                failed.append((ticker, "No data returned"))
                status = "empty"
                empty_streak += 1
        except Exception as e:
            failed.append((ticker, str(e)))
            status = "error"
            empty_streak += 1

        if progress_callback:
            try:
                progress_callback(i + 1, total, ticker, status)
            except Exception:
                pass

        # Rate-limit detection: if we just hit the empty-streak threshold,
        # pause and reset the streak. After N trips, give up — Yahoo isn't
        # letting up and continuing wastes the CI run.
        if empty_streak >= _EMPTY_STREAK_TRIP:
            trips += 1
            print(f"  ⚠ {empty_streak} consecutive empty returns at {ticker} — "
                  f"likely rate-limited. Cooling down {_RATE_LIMIT_COOLDOWN}s "
                  f"(trip {trips}/{_RATE_LIMIT_MAX_TRIPS}).")
            if trips >= _RATE_LIMIT_MAX_TRIPS:
                print(f"  ✗ Rate-limited persistently — aborting refresh at "
                      f"{i + 1}/{total} to avoid burning the rest of the run.")
                break
            time.sleep(_RATE_LIMIT_COOLDOWN)
            empty_streak = 0
        elif _INTER_TICKER_DELAY:
            time.sleep(_INTER_TICKER_DELAY)

    print(f"\nRefresh complete!")
    print(f"  Successful: {success_count}/{len(universe)}")
    if len(failed) > 0:
        print(f"  Failed: {len(failed)}")
        for ticker, reason in failed[:5]:
            print(f"    - {ticker}: {reason}")
        if len(failed) > 5:
            print(f"    ... and {len(failed) - 5} more")
