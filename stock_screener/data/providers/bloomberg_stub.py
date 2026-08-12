"""Stub Bloomberg Terminal (BLPAPI) provider.

Placeholder until the buddy with the Bloomberg Anywhere license fills
in the actual blpapi calls. Kept in-tree so DATA_PROVIDER=bloomberg
routes here instead of exploding, and so there's one obvious file to
open when it's time to wire it up.

To implement:

  1. Install blpapi from Bloomberg's PyPI mirror on the machine that
     will run the sync agent (needs Terminal running + logged in with
     B-Unit — Fly cannot host this):
         pip install --index-url=https://blpapi.bloomberg.com/repository/releases/python/simple/ blpapi

  2. Fill in fetch_ohlcv / fetch_ohlcv_bulk below. Reference:
     https://bloomberg.github.io/blpapi-docs/python/3.19/
     https://data.bloomberglp.com/professional/sites/10/2017/03/BLPAPI-Core-Developer-Guide.pdf
     The relevant service is //blp/refdata, request type
     HistoricalDataRequest. Batch multiple securities in one request
     for the bulk path (that's BLPAPI's whole reason to exist over a
     REST loop).

  3. Bloomberg ticker format: "AAPL" → "AAPL US Equity"
     Field name map: open→PX_OPEN, high→PX_HIGH, low→PX_LOW,
                     close→PX_LAST, volume→PX_VOLUME

  4. Run scripts/test_bloomberg.py (to be written alongside) as a
     smoke test that pulls AAPL for the last 30 days and prints the
     DataFrame. If that works, flip DATA_PROVIDER=bloomberg on the
     buddy's sync agent.

Until any of that happens, calling this provider raises a clear error
so a mis-flip of DATA_PROVIDER on Fly (where blpapi can't work at all)
surfaces immediately instead of returning empty frames silently.
"""

from __future__ import annotations

import pandas as pd


class BloombergNotAvailable(RuntimeError):
    pass


_ERR = (
    "BloombergStubProvider is a placeholder. Fill in the blpapi calls "
    "in stock_screener/data/providers/bloomberg_stub.py, install "
    "blpapi on a machine with the Terminal running + logged in, and "
    "route data pulls through that machine. Note that BLPAPI cannot "
    "run on Fly — the sync agent must live on the Terminal machine."
)


class BloombergStubProvider:
    name = "bloomberg"
    label = "Bloomberg (BLPAPI — stub)"

    def fetch_ohlcv(
        self, ticker: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        raise BloombergNotAvailable(_ERR)

    def fetch_ohlcv_bulk(
        self, tickers: list[str], start_date: str, end_date: str
    ) -> dict[str, pd.DataFrame]:
        raise BloombergNotAvailable(_ERR)
