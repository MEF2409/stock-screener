"""yfinance-backed MarketDataProvider.

Preserves the existing behavior verbatim — retry + backoff on empty
responses, MultiIndex flattening, chunked bulk downloads. Anything
that used to happen in stock_screener/data/fetcher.py now happens
here; fetcher.py became a thin dispatcher.
"""

from __future__ import annotations

import time

import pandas as pd
import yfinance as yf


# Yahoo throttles after a few hundred sequential yf.download calls. The
# first symptom is empty DataFrames returned silently — every ticker
# comes back as "possibly delisted; no price data found" until the
# limit clears. Retry with exponential backoff and treat empty as
# transient.
_MAX_ATTEMPTS = 4
_BASE_SLEEP = 1.5  # seconds; doubles each retry
_CHUNK_SIZE = 200  # tickers per yf.download bulk request


class YFinanceProvider:
    name = "yfinance"
    label = "yfinance (free)"

    def fetch_ohlcv(self, ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
        for attempt in range(_MAX_ATTEMPTS):
            try:
                df = yf.download(
                    ticker, start=start_date, end=end_date,
                    progress=False, auto_adjust=True, threads=False,
                )
            except Exception:
                df = pd.DataFrame()

            if not df.empty:
                # yfinance returns a MultiIndex on columns even for a
                # single ticker (e.g. ('Open', 'ABT')). Flatten by
                # dropping the ticker level. Positional rename is unsafe
                # — yfinance's column order changed to alphabetical
                # (Close, High, Low, Open, Volume), which silently
                # swapped Open/Close.
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df.reset_index()
                df = df[["Date", "Open", "High", "Low", "Close", "Volume"]]
                df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
                return df

            if attempt < _MAX_ATTEMPTS - 1:
                time.sleep(_BASE_SLEEP * (2 ** attempt))

        return pd.DataFrame()

    def fetch_ohlcv_bulk(
        self, tickers: list[str], start_date: str, end_date: str
    ) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        if not tickers:
            return out

        for i in range(0, len(tickers), _CHUNK_SIZE):
            chunk = tickers[i:i + _CHUNK_SIZE]
            bulk: pd.DataFrame | None = None
            for attempt in range(_MAX_ATTEMPTS):
                try:
                    bulk = yf.download(
                        tickers=chunk, start=start_date, end=end_date,
                        progress=False, auto_adjust=True, threads=True,
                        group_by="ticker",
                    )
                except Exception:
                    bulk = None
                if bulk is not None and not bulk.empty:
                    break
                if attempt < _MAX_ATTEMPTS - 1:
                    time.sleep(_BASE_SLEEP * (2 ** attempt))

            if bulk is None or bulk.empty:
                continue

            # Bulk returns a column-MultiIndex (ticker, field) when
            # len(chunk)>1; flat single-ticker frame when len(chunk)==1.
            if isinstance(bulk.columns, pd.MultiIndex):
                for ticker in chunk:
                    if ticker not in bulk.columns.get_level_values(0):
                        continue
                    tdf = bulk[ticker].dropna(how="all").reset_index()
                    if tdf.empty or "Open" not in tdf.columns:
                        continue
                    tdf = tdf[["Date", "Open", "High", "Low", "Close", "Volume"]]
                    tdf["Date"] = pd.to_datetime(tdf["Date"]).dt.strftime("%Y-%m-%d")
                    if not tdf.dropna().empty:
                        out[ticker] = tdf
            else:
                tdf = bulk.dropna(how="all").reset_index()
                if "Open" not in tdf.columns:
                    continue
                tdf = tdf[["Date", "Open", "High", "Low", "Close", "Volume"]]
                tdf["Date"] = pd.to_datetime(tdf["Date"]).dt.strftime("%Y-%m-%d")
                out[chunk[0]] = tdf

        return out
