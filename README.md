# Stock Screener Dashboard

Daily stock screener that flags NYSE/NASDAQ stocks matching four technical setups:

- **Bull #1 — Runaway Gap**: gap up on heavy volume (≥1.3× 30-day avg)
- **Bull #2 — Bullish Divergence**: new 52-week low with RSI higher than at the previous 52-week low
- **Bear #1 — Bearish Divergence**: new 52-week high with RSI lower than at the previous 52-week high
- **Bear #2 — Gap Up on Normal Volume**: gap up on normal volume below the 50/100/200-day moving averages

Stocks with earnings within the next 14 calendar days are filtered out across all scanners.

## Stack

- Python 3.10+
- Data: `yfinance` (architected so Finnhub can be swapped in later)
- Storage: SQLite (single file in `db/`)
- Dashboard: Streamlit
- Scheduling: daily after market close (4pm ET) — wired in last

## Universe

- NYSE + NASDAQ common stocks
- Price ≥ $5
- 30-day average daily volume ≥ 500,000 shares
- Recomputed weekly

## Project layout

```
stock_screener/
  data/         # yfinance fetcher + SQLite I/O
  universe/     # universe builder (price/volume filter)
  indicators/   # RSI, moving averages, avg volume
  earnings/     # next-earnings dates + 14-day filter
  scanners/     # the four scanners
  dashboard/    # Streamlit app
scripts/        # daily refresh entry points
tests/
db/             # SQLite database (gitignored)
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Persistence rule

Flags are evaluated fresh each day against that day's data. No history of past signals is shown — old signals are old news.

## Out of scope for v1

See [TODO.md](TODO.md).
