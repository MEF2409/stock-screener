# Stock Screener Dashboard — Usage Guide

## Quick Start

### 1. Setup (one time)

```bash
cd stock-screener
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run the Streamlit dashboard

```bash
source .venv/bin/activate
streamlit run stock_screener/dashboard/app.py
```

Opens at `http://localhost:8501` by default.

### 3. Or run the daily batch job

```bash
source .venv/bin/activate
python scripts/daily_refresh.py
```

Outputs results to `results/` directory as JSON and CSV.

---

## System Overview

### Data Layer
- **yfinance**: Fetch OHLCV data for stocks
- **SQLite**: Store price data, universe list, earnings dates
- **Bulk refresh**: Pull and update 1 year of data for all stocks

### Universe
- NYSE + NASDAQ listed common stocks
- Price ≥ $5
- 30-day average daily volume ≥ 500,000 shares
- Recomputed weekly (or on-demand in dashboard)

### Indicators
- **RSI(14)**: Relative Strength Index
- **MA(50/100/200)**: Simple moving averages
- **Avg Volume(30d)**: 30-day average trading volume
- **52-week extrema**: High/low prices and their RSI values

### Universal Filter
**Applied to ALL scanners**: Skip stocks with earnings announcements within the next 14 calendar days. Post-earnings stocks (news already priced in) are not filtered.

### Four Scanners

#### Bull #1 — Runaway Gap
- Today's open > yesterday's close (gap up)
- Today's volume ≥ 1.3 × 30-day average daily volume
- **Interpretation**: Bullish momentum on heavy accumulation

#### Bull #2 — Bullish Divergence
- Today's price is at a new 52-week low
- Today's RSI(14) > RSI at the date of the previous 52-week low
- **Interpretation**: Price made a lower low, but RSI made a higher low — selling momentum is slowing

#### Bear #1 — Bearish Divergence
- Today's price is at a new 52-week high
- Today's RSI(14) < RSI at the date of the previous 52-week high
- **Interpretation**: Price made a higher high, but RSI made a lower high — buying momentum is slowing

#### Bear #2 — Gap Up on Normal Volume
- Today's open > yesterday's close (gap up)
- Today's volume < 1.3 × 30-day average (normal volume)
- Today's open < 50-day MA AND 100-day MA AND 200-day MA
- **Interpretation**: Gap likely to fill; short candidate

### Persistence Rule
Flags are evaluated fresh each day against that day's data. No historical signals are shown — old signals are old news (already priced in).

---

## Dashboard

### Tabs
1. **Bull #1 — Runaway Gap**: Bullish gap-up signals
2. **Bull #2 — Bullish Divergence**: Bullish RSI divergence signals
3. **Bear #1 — Bearish Divergence**: Bearish RSI divergence signals
4. **Bear #2 — Gap Up Normal Volume**: Bearish gap-fill candidate signals

Each tab shows:
- Number of flags found
- Results table with ticker, price, volume, RSI

### Detail View (Sidebar)
Enter a ticker symbol to view detailed charts:
- **Price Chart**: Close price + 50/100/200-day moving averages (last 100 days)
- **RSI Chart**: RSI(14) with overbought (70) / oversold (30) zones
- **Volume Chart**: Daily volume + 30-day average
- **OHLCV Table**: Last 10 trading days of raw data

### Data Management (Sidebar)
- **Update Universe**: Recompute list of qualifying stocks (price, volume filters)
- **Refresh Data**: Fetch and update 1 year of OHLCV for all universe stocks

---

## Daily Batch Job

### Manual Run
```bash
python scripts/daily_refresh.py
```

### Automated (Cron)
Edit your crontab to run daily after market close:

```bash
crontab -e
```

Add a line like:
```
# Run daily refresh at 4:15pm ET (after market close)
15 16 * * Mon-Fri /usr/bin/python3 /Users/username/stock-screener/scripts/daily_refresh.py >> /tmp/screener.log 2>&1
```

(Adjust path to your `.venv/bin/python` if using venv.)

### Output
Results are saved to `results/` directory:
- `results_YYYYMMDD_HHMMSS.json`: Summary with all flags
- `runaway_gap_YYYYMMDD_HHMMSS.csv`: Detailed runaway gap results
- `bullish_divergence_YYYYMMDD_HHMMSS.csv`: (if any)
- `bearish_divergence_YYYYMMDD_HHMMSS.csv`: (if any)
- `gap_up_normal_volume_YYYYMMDD_HHMMSS.csv`: Detailed short candidate results

---

## Architecture

```
stock_screener/
  data/
    db.py            # SQLite schema & connections
    fetcher.py       # yfinance → SQLite I/O
    bulk_refresh.py  # Fetch for entire universe
  
  universe/
    builder.py       # Qualify stocks by price/volume filters
  
  indicators/
    indicators.py    # RSI, moving averages, 52-week extrema
  
  earnings/
    earnings.py      # Next earnings dates + 14-day filter
  
  scanners/
    scanners.py      # Four scanners + universal filter
  
  dashboard/
    app.py           # Streamlit app

scripts/
  daily_refresh.py   # Batch job (orchestrates workflow)
  test_*.py          # Verification scripts
```

---

## Swapping Data Sources

Currently uses **yfinance**. To switch to **Finnhub** or another provider:

1. Update `stock_screener/data/fetcher.py`: replace `yfinance.download()` with your API call
2. Keep the same DataFrame format: Date, Open, High, Low, Close, Volume
3. Rest of system remains unchanged (SQLite, indicators, scanners work with any OHLCV data)

---

## Performance Notes

- **Universe build**: ~2-3 minutes (downloads 60d of data per stock to compute volume)
- **Bulk refresh**: ~30-60 seconds for 35 stocks (depends on internet speed)
- **Scanner run**: ~2-5 seconds for 35 stocks
- **Dashboard**: Fast (queries local SQLite)

For a full universe (1000+ stocks), consider:
- Running daily refresh at off-peak hours
- Batching tickers to avoid rate limits on yfinance
- Caching universe list more frequently (e.g., weekly instead of daily)

---

## Out of Scope

See `TODO.md` for v2 ideas (backtesting, VIX integration, alerts, etc.).

---

## Support

Built with yfinance, SQLite, Pandas, Plotly, Streamlit. For issues:
- Check that data is being fetched: `python scripts/test_data_layer.py`
- Verify universe: `python scripts/test_universe_builder.py`
- Test scanners: `python scripts/test_scanners.py`
