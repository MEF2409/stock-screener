"""Bulk fetch and refresh OHLCV data for the universe."""

from datetime import datetime, timedelta
import pandas as pd

from stock_screener.data.fetcher import fetch_ohlcv_bulk, store_ohlcv
from stock_screener.universe.builder import get_universe


_CHUNK_SIZE = 200  # tickers per yf.download bulk request


def refresh_all_ohlcv(days_back: int = 365, progress_callback=None) -> None:
    """
    Fetch and refresh OHLCV data for all stocks in the universe.

    Uses yfinance bulk (multi-ticker) downloads — one request per 200-ticker
    chunk instead of one per ticker. The per-ticker path took 15-30 min for
    the full universe and timed out the daily-refresh SSH session; bulk
    collapses that to ~1 min.

    Args:
        days_back: Number of trading days to fetch (default 365 ≈ 1 year)
        progress_callback: Optional fn(done:int, total:int, ticker:str, status:str)
            called after each chunk completes (ticker is the last in the chunk).
    """
    universe = get_universe()

    if len(universe) == 0:
        print("Universe is empty. Update universe first with update_universe().")
        return

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    tickers = universe["ticker"].tolist()
    total = len(tickers)
    print(f"Refreshing OHLCV data for {total} stocks")
    print(f"Date range: {start_date} to {end_date}")

    success_count = 0
    done = 0
    failed = []

    for i in range(0, total, _CHUNK_SIZE):
        chunk = tickers[i:i + _CHUNK_SIZE]
        bulk = fetch_ohlcv_bulk(chunk, start_date, end_date, chunk_size=_CHUNK_SIZE)
        for ticker in chunk:
            done += 1
            df = bulk.get(ticker)
            status = "ok"
            if df is not None and len(df) > 0:
                try:
                    store_ohlcv(ticker, df)
                    success_count += 1
                except Exception as e:
                    failed.append((ticker, str(e)))
                    status = "error"
            else:
                failed.append((ticker, "No data returned"))
                status = "empty"
            if progress_callback:
                try:
                    progress_callback(done, total, ticker, status)
                except Exception:
                    pass
        print(f"  Progress: {min(i + _CHUNK_SIZE, total)}/{total} (success: {success_count})")

    print(f"\nRefresh complete!")
    print(f"  Successful: {success_count}/{total}")
    if len(failed) > 0:
        print(f"  Failed: {len(failed)}")
        for ticker, reason in failed[:5]:
            print(f"    - {ticker}: {reason}")
        if len(failed) > 5:
            print(f"    ... and {len(failed) - 5} more")
