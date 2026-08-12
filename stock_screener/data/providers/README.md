# Data providers

The stock screener talks to market-data sources through a
`MarketDataProvider` abstraction. `stock_screener/data/fetcher.py`'s
public functions (`fetch_ohlcv`, `fetch_ohlcv_bulk`) delegate to
whichever provider the `DATA_PROVIDER` env var selects. All
scanners, dashboards, and cron scripts call the fetcher functions
without knowing which source is behind them.

## Providers

| `DATA_PROVIDER` | Class | Notes |
|---|---|---|
| `yfinance` *(default)* | `YFinanceProvider` | Free. Rate-limits often. |
| `massive` | `MassiveProvider` | massive.com (formerly polygon.io). Needs `MASSIVE_API_KEY`. Starter tier ($29/mo) has unlimited API + grouped-daily bulk. |
| `bloomberg` | `BloombergStubProvider` | Placeholder. Fill in `bloomberg_stub.py` with `blpapi` calls on a machine that has a live Bloomberg Terminal / Anywhere login. |

## Enabling Massive on Fly

```
flyctl secrets set MASSIVE_API_KEY=<key> --app market-pulse-mef
flyctl secrets set DATA_PROVIDER=massive --app market-pulse-mef
```

The secrets change triggers a Fly redeploy. Once the machine is up,
the sidebar "Data Source" chip will show **Massive (Starter)** and the
next scheduled cron (or a "Run now" click) will pull via Massive
instead of yfinance. Every scanner, indicator, and cache stays
identical — you're just swapping the underlying data source.

To go back:

```
flyctl secrets unset DATA_PROVIDER --app market-pulse-mef
```

(Absent = default = `yfinance`.)

## Adding a new provider

1. Drop `my_provider.py` next to the others.
2. Define a class with a `name` attribute + `fetch_ohlcv` +
   `fetch_ohlcv_bulk` methods matching the `MarketDataProvider`
   protocol in `base.py`. Return DataFrames with columns
   `Date, Open, High, Low, Close, Volume` (Date as `YYYY-MM-DD`
   strings).
3. Register in `PROVIDERS` in `__init__.py`.
4. Set `DATA_PROVIDER=my_provider`.

Nothing else changes.

## Filling in the Bloomberg stub

The buddy with a Bloomberg Anywhere license needs a machine where the
Terminal software is running + logged in (BLPAPI connects to a local
`bbcomm` process spawned by Terminal login — Fly cannot host this).
Once that's set up:

```
pip install --index-url=https://blpapi.bloomberg.com/repository/releases/python/simple/ blpapi
```

Then edit `bloomberg_stub.py`:

1. Replace `BloombergStubProvider` methods with real `blpapi.Session`
   + `//blp/refdata` + `HistoricalDataRequest` code.
2. Ticker format: `"AAPL"` → `"AAPL US Equity"`.
3. Field map: `open→PX_OPEN, high→PX_HIGH, low→PX_LOW,
   close→PX_LAST, volume→PX_VOLUME`.
4. Batch multiple securities per request for the bulk path — that's
   the reason BLPAPI exists over a REST loop.
5. Write `scripts/test_bloomberg.py` that pulls AAPL for the last 30
   days and prints the DataFrame. If that works, flip
   `DATA_PROVIDER=bloomberg` on the sync agent.

The stub raises a clear error today so a wrong flip on Fly (where
blpapi can't work at all) surfaces immediately instead of returning
empty frames silently.
