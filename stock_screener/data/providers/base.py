"""The MarketDataProvider contract every source must implement."""

from __future__ import annotations

from typing import Protocol

import pandas as pd


class MarketDataProvider(Protocol):
    """Contract for a source of daily OHLCV data.

    IMPORTANT — both methods must return DataFrames with EXACTLY these
    columns, in this order, so downstream scanners / indicators /
    storage don't have to know which provider they're getting:

        Date   — string, 'YYYY-MM-DD'
        Open   — float
        High   — float
        Low    — float
        Close  — float, split/dividend adjusted
        Volume — int

    Empty DataFrame (not None) when a ticker has no data for the
    requested range.
    """

    #: Short lowercase name matching the DATA_PROVIDER env value.
    name: str

    #: Human-readable label for the Data Health chip.
    label: str

    def fetch_ohlcv(
        self, ticker: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """Fetch daily OHLCV for a single ticker between start_date and
        end_date (both YYYY-MM-DD, inclusive of start, exclusive of end
        to match the historic yfinance semantic — providers normalize
        internally if their native API differs)."""
        ...

    def fetch_ohlcv_bulk(
        self, tickers: list[str], start_date: str, end_date: str
    ) -> dict[str, pd.DataFrame]:
        """Fetch daily OHLCV for many tickers at once. Return a dict
        keyed by ticker — tickers with no data are omitted (not None-
        valued). Implementations should be significantly faster than a
        loop over fetch_ohlcv when the provider exposes a bulk / grouped
        endpoint; otherwise it's fine to loop internally."""
        ...
