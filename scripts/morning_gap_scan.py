#!/usr/bin/env python3
"""Morning Gap Scan — fires at 9:32 AM ET, just after the open.

All universe tickers gapping ≥ 2% from yesterday's close get listed,
sorted by absolute gap %, with the earnings catalyst flag. Sent as an
alert and persisted to scan_history so backtests can replay the open.

Why 9:32 vs true pre-market:
  Real pre-market data (8:00-9:30 AM ET) needs a paid feed (Finnhub Pro,
  Polygon, etc.). yfinance's prepost intraday is per-ticker and would take
  ~30 min over the 2k-ticker universe. 9:32 is the cheapest reliable time
  to get today's open print bulk via the standard daily endpoint.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from stock_screener.data.db import init_db, get_connection
from stock_screener.data.fetcher import fetch_ohlcv, store_ohlcv, get_ohlcv
from stock_screener.universe.builder import get_universe
from stock_screener.earnings.earnings import had_earnings_within_past_days
from stock_screener.alerts.notifier import send_all


GAP_THRESHOLD_PCT = 2.0


def refresh_today_ohlcv(tickers: list[str]) -> int:
    today = datetime.now().date()
    start = (today - timedelta(days=5)).isoformat()
    end = (today + timedelta(days=1)).isoformat()
    success = 0
    for i, ticker in enumerate(tickers):
        if (i + 1) % 250 == 0:
            print(f"  refresh {i + 1}/{len(tickers)} success={success}")
        try:
            df = fetch_ohlcv(ticker, start, end)
            if not df.empty:
                store_ohlcv(ticker, df)
                success += 1
        except Exception:
            continue
    return success


def find_gappers(tickers: list[str]) -> list[dict]:
    gappers = []
    for ticker in tickers:
        try:
            df = get_ohlcv(ticker)
            if len(df) < 2:
                continue
            today = df.iloc[-1]
            yesterday = df.iloc[-2]
            prev_close = float(yesterday["Close"])
            today_open = float(today["Open"])
            if prev_close <= 0:
                continue
            gap_pct = (today_open - prev_close) / prev_close * 100
            if abs(gap_pct) < GAP_THRESHOLD_PCT:
                continue
            gappers.append({
                "ticker": ticker,
                "open": today_open,
                "close": prev_close,  # gap reference
                "gap_pct": gap_pct,
                "volume": int(today["Volume"]),
                "catalyst": "earnings" if had_earnings_within_past_days(ticker, days=2) else "none",
            })
        except Exception:
            continue
    gappers.sort(key=lambda r: abs(r["gap_pct"]), reverse=True)
    return gappers


def main():
    print("=" * 60)
    print(f"Morning Gap Scan — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    init_db()
    universe = get_universe()
    if universe.empty:
        print("Universe empty. Run daily_refresh.py first.")
        return
    tickers = universe["ticker"].tolist()

    print(f"\n1. Refreshing today's bars for {len(tickers)} tickers...")
    success = refresh_today_ohlcv(tickers)
    print(f"   ✓ Refreshed {success}/{len(tickers)}")

    print(f"\n2. Finding gappers (|gap| ≥ {GAP_THRESHOLD_PCT}%)...")
    gappers = find_gappers(tickers)
    ups = [g for g in gappers if g["gap_pct"] > 0]
    downs = [g for g in gappers if g["gap_pct"] < 0]
    earnings_count = sum(1 for g in gappers if g["catalyst"] == "earnings")
    print(f"   ✓ {len(gappers)} gappers ({len(ups)} up, {len(downs)} down, {earnings_count} on earnings)")

    # Reuse the alerts formatter — slot the gappers into a virtual scanner so the
    # existing notifier prints them with the per-ticker context (gap % + catalyst).
    alert_payload = {
        "runaway_gap": [g for g in ups if g["catalyst"] == "earnings" or abs(g["gap_pct"]) >= 3.0],
        "bullish_div": [],
        "bearish_div": [],
        "gap_up_normal_vol": [g for g in downs[:30]],  # gap-DOWNs go in the "fade" lane to surface them
    }

    print(f"\n3. Sending alert...")
    delivered = send_all(alert_payload)
    for channel, ok in delivered.items():
        if ok is None:
            print(f"   - {channel}: not configured")
        elif ok:
            print(f"   ✓ {channel}: sent")
        else:
            print(f"   ✗ {channel}: failed")

    # Persist to scan_history for replay/backtest
    today_tag = datetime.now().date().isoformat() + "_premarket"
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM scan_history WHERE run_date = ?", (today_tag,))
    for g in gappers:
        scanner = "GapUp" if g["gap_pct"] > 0 else "GapDown"
        cur.execute(
            "INSERT OR REPLACE INTO scan_history (run_date, scanner, ticker) VALUES (?, ?, ?)",
            (today_tag, scanner, g["ticker"]),
        )
    conn.commit()
    conn.close()

    print(f"\nDone: {len(gappers)} gappers logged to scan_history.")


if __name__ == "__main__":
    main()
