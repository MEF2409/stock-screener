"""massive.com (formerly polygon.io) REST-backed provider.

Two endpoints do the work:

  1. Grouped daily aggregates —
       /v2/aggs/grouped/locale/us/market/stocks/{date}?adjusted=true
     Returns OHLCV for every US stock on that single date in ONE call.
     This is the bulk power move: our historic yfinance path had to
     make N × chunks per day; here we can back-fill an entire universe
     for a year with ~250 HTTP calls (one per trading day) regardless
     of ticker count.

  2. Per-ticker aggregates over a range —
       /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}?adjusted=true
     Used for the single-ticker path (dashboard drill-in, watchlist
     backfill). One request returns the full date range for one ticker.

API key comes from the MASSIVE_API_KEY env var. On Fly:
  flyctl secrets set MASSIVE_API_KEY=<key>
  flyctl secrets set DATA_PROVIDER=massive

Rate: Starter tier is unlimited API calls, so we parallelize the
grouped-daily loop with a thread pool. Keep concurrency modest so we
don't step on ourselves or trip anti-abuse.
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Iterable

import pandas as pd
import requests


_BASE_URL = "https://api.massive.com"        # alias for api.polygon.io
_TIMEOUT = 20                                 # seconds per request
_MAX_ATTEMPTS = 4                             # retry with backoff on 429/5xx
_BASE_SLEEP = 0.75                            # doubles each retry
_GROUPED_CONCURRENCY = 8                      # parallel /grouped calls
_SESSION_LOCK = threading.Lock()
_SESSION: requests.Session | None = None


def _session() -> requests.Session:
    """Shared thread-safe requests.Session so connection pooling helps
    the parallel grouped-daily fanout. The Session is stateless
    otherwise; API key goes on the query string per request."""
    global _SESSION
    if _SESSION is None:
        with _SESSION_LOCK:
            if _SESSION is None:
                s = requests.Session()
                s.headers.update({"User-Agent": "market-pulse/1.0"})
                _SESSION = s
    return _SESSION


class MassiveApiError(RuntimeError):
    pass


def _api_key() -> str:
    key = os.environ.get("MASSIVE_API_KEY") or os.environ.get("POLYGON_API_KEY")
    if not key:
        raise MassiveApiError(
            "MASSIVE_API_KEY env var is not set. Get a key from "
            "https://massive.com and either export it locally or "
            "`flyctl secrets set MASSIVE_API_KEY=<key>`."
        )
    return key


def _get_json(url: str, params: dict | None = None) -> dict:
    """GET with retry on 429 (rate limit — shouldn't happen on Starter
    but be safe) and 5xx. Raise MassiveApiError on final failure."""
    params = dict(params or {})
    params["apiKey"] = _api_key()
    last_exc: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            r = _session().get(url, params=params, timeout=_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                # Retryable — back off
                last_exc = MassiveApiError(
                    f"HTTP {r.status_code} from {url}: {r.text[:200]}"
                )
            else:
                # Non-retryable client error (401 bad key, 404, etc.)
                raise MassiveApiError(
                    f"HTTP {r.status_code} from {url}: {r.text[:200]}"
                )
        except requests.RequestException as exc:
            last_exc = exc
        if attempt < _MAX_ATTEMPTS - 1:
            time.sleep(_BASE_SLEEP * (2 ** attempt))
    raise MassiveApiError(f"Request failed after {_MAX_ATTEMPTS} attempts: {last_exc}")


def _trading_dates(start_date: str, end_date: str) -> list[str]:
    """Weekdays between start_date (inclusive) and end_date (exclusive)
    — matches yfinance semantics. Doesn't account for exchange
    holidays; those simply return empty from the grouped-daily endpoint
    and we skip them."""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    out = []
    cur = start
    while cur < end:
        if cur.weekday() < 5:   # Mon-Fri
            out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


def _fetch_grouped_day(date_str: str) -> list[dict]:
    """One trading day's OHLCV for every US stock. Returns [] on
    non-trading days (endpoint gives resultsCount=0)."""
    url = f"{_BASE_URL}/v2/aggs/grouped/locale/us/market/stocks/{date_str}"
    data = _get_json(url, {"adjusted": "true"})
    return data.get("results") or []


class MassiveProvider:
    name = "massive"
    label = "Massive (Starter)"

    def fetch_ohlcv(
        self, ticker: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """Single-ticker range — one HTTP call to the aggs range endpoint."""
        # Massive's range endpoint 'to' is inclusive; yfinance was
        # exclusive. Subtract one day so behavior matches.
        to_inclusive = (
            datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=1)
        ).strftime("%Y-%m-%d")
        url = (
            f"{_BASE_URL}/v2/aggs/ticker/{ticker.upper()}/range/1/day/"
            f"{start_date}/{to_inclusive}"
        )
        try:
            data = _get_json(url, {"adjusted": "true", "sort": "asc", "limit": 50000})
        except MassiveApiError:
            return pd.DataFrame()
        results = data.get("results") or []
        if not results:
            return pd.DataFrame()
        return _bars_to_df(results)

    def fetch_ohlcv_bulk(
        self, tickers: list[str], start_date: str, end_date: str
    ) -> dict[str, pd.DataFrame]:
        """Bulk via grouped-daily fanout — one HTTP call per trading day,
        each covering every ticker. Parallelized across days.

        For a 365-day universe refresh this is ~250 concurrent HTTP
        calls (concurrency capped at _GROUPED_CONCURRENCY) instead of
        ~10 chunks × N days of per-ticker yfinance downloads. Wall
        clock: seconds instead of minutes.
        """
        if not tickers:
            return {}
        # Fail loud on missing key before the fanout — a silent empty
        # result would look like 'refresh succeeded, zero data' in the
        # job_runs table and mislead the Data Health panel.
        _api_key()
        wanted = set(t.upper() for t in tickers)
        dates = _trading_dates(start_date, end_date)

        # {ticker_upper: [bar_dict, ...]}, populated concurrently
        by_ticker: dict[str, list[dict]] = {t: [] for t in wanted}

        with ThreadPoolExecutor(max_workers=_GROUPED_CONCURRENCY) as pool:
            futures = {pool.submit(_fetch_grouped_day, d): d for d in dates}
            for fut in as_completed(futures):
                date_str = futures[fut]
                try:
                    bars = fut.result()
                except MassiveApiError:
                    # Skip individual failed days — full-run still
                    # succeeds even if 1-2 dates hiccup.
                    continue
                for b in bars:
                    tkr = b.get("T")
                    if tkr in wanted:
                        # Grouped-daily bars don't carry the date in a
                        # field we want to trust; stamp it from the
                        # request URL instead.
                        b["__date"] = date_str
                        by_ticker[tkr].append(b)

        # Convert per-ticker bar lists to DataFrames in the required
        # shape. Sort by date since concurrent completion is arbitrary.
        out: dict[str, pd.DataFrame] = {}
        for tkr, bars in by_ticker.items():
            if not bars:
                continue
            bars.sort(key=lambda b: b["__date"])
            df = _bars_to_df(bars, date_field="__date")
            if not df.empty:
                out[tkr] = df
        return out


def _bars_to_df(bars: Iterable[dict], date_field: str = "t") -> pd.DataFrame:
    """Convert a list of Polygon/Massive bar dicts into the canonical
    Date/Open/High/Low/Close/Volume shape scanners expect."""
    rows = []
    for b in bars:
        if date_field == "t":
            # Millisecond epoch → YYYY-MM-DD
            ts = b.get("t")
            if ts is None:
                continue
            date_str = datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")
        else:
            date_str = b.get(date_field)
            if date_str is None:
                continue
        try:
            rows.append({
                "Date": date_str,
                "Open": float(b["o"]),
                "High": float(b["h"]),
                "Low": float(b["l"]),
                "Close": float(b["c"]),
                "Volume": int(b["v"]),
            })
        except (KeyError, TypeError, ValueError):
            # Skip malformed bars — better a shorter series than a
            # blown-up refresh.
            continue
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)
