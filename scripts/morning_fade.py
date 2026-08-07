#!/usr/bin/env python3
"""Morning Fade run — fires at ~9:45-10am ET, after market open.

Why separate from daily_refresh.py:
  Fade signals are actionable at the open (gap up + below MAs). The 4:30pm
  cron flags them after the day is done — too late to trade. This script
  refreshes only today's prices and runs only the Fade scanner.

What it does:
  1. Refresh today's OHLCV for the universe (single-day fetch is cheap)
  2. Run scan_gap_up_normal_volume across the universe
  3. Send alerts via the same channels as daily_refresh

Caveats:
  - The "normal volume" check compares today's volume vs the 30-day average.
    At 9:45 AM, today's volume is naturally low — most candidates will trip
    the "normal volume" condition trivially. That's actually fine for the
    Fade thesis (we want gaps NOT supported by heavy buying).
  - yfinance's intraday data has occasional lag. Don't expect tick-perfect.
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from stock_screener.data.db import init_db, get_connection
from stock_screener.data.fetcher import fetch_ohlcv_bulk, store_ohlcv
from stock_screener.jobs import record_run
from stock_screener.universe.builder import get_universe
from stock_screener.scanners.scanners import scan_gap_up_normal_volume
from stock_screener.alerts.notifier import send_all


def refresh_today_ohlcv(tickers: list[str]) -> int:
    """Bulk-fetch today's bar (with a few days of context for gap math) for
    every ticker in one batched call and upsert. Returns success count."""
    today = datetime.now().date()
    start = (today - timedelta(days=5)).isoformat()
    # yfinance treats `end` as exclusive — bump to tomorrow so today's
    # in-progress bar is returned. Without this, the latest bar in the DB
    # is yesterday's, and the Fade scanner compares yesterday's open vs.
    # the prior day's close (a stale, 1-day-old gap check).
    end = (today + timedelta(days=1)).isoformat()

    bulk = fetch_ohlcv_bulk(tickers, start, end, chunk_size=200)
    success = 0
    for ticker, df in bulk.items():
        try:
            store_ohlcv(ticker, df)
            success += 1
        except Exception:
            continue
    print(f"  bulk-fetched {len(bulk)}/{len(tickers)} tickers; stored {success}")
    return success


def main():
    print("=" * 60)
    print(f"Morning Fade Run — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    init_db()
    universe = get_universe()
    if universe.empty:
        print("Universe is empty. Run daily_refresh.py first.")
        return
    tickers = universe["ticker"].tolist()

    print(f"\n1. Refreshing today's bars for {len(tickers)} tickers...")
    success = refresh_today_ohlcv(tickers)
    print(f"   ✓ Refreshed {success}/{len(tickers)}")

    print(f"\n2. Running Fade scanner...")
    flagged = []
    total = len(tickers)
    for i, ticker in enumerate(tickers):
        r = scan_gap_up_normal_volume(ticker)
        if r["flagged"]:
            flagged.append(r)
        # Heartbeat keeps the flyctl ssh tunnel from idle-timing-out
        # at ~5 min on the silent scanner pass.
        if (i + 1) % 250 == 0:
            print(f"  scanner: {i + 1}/{total} (flagged so far: {len(flagged)})",
                  flush=True)
    print(f"   ✓ {len(flagged)} Fade candidates")

    # Reuse the same alert formatter — pass {fade scanner only}
    alert_payload = {
        "runaway_gap": [],
        "bullish_div": [],
        "bearish_div": [],
        "gap_up_normal_vol": flagged,
    }

    print(f"\n3. Sending morning alerts...")
    delivered = send_all(alert_payload)
    for channel, ok in delivered.items():
        if ok is None:
            print(f"   - {channel}: not configured")
        elif ok:
            print(f"   ✓ {channel}: sent")
        else:
            print(f"   ✗ {channel}: failed")

    # Append to scan_history with a "morning" run_date marker so dashboard can
    # show that this snapshot was a morning run, not the EOD one
    today = datetime.now().date().isoformat() + "_morning"
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM scan_history WHERE run_date = ?", (today,))
    for r in flagged:
        cur.execute(
            "INSERT OR REPLACE INTO scan_history (run_date, scanner, ticker) VALUES (?, ?, ?)",
            (today, "Fade", r["ticker"]),
        )
    conn.commit()
    conn.close()

    print(f"\nDone: {len(flagged)} Fade candidates flagged.")


if __name__ == "__main__":
    import os
    triggered_by = os.environ.get("JOB_TRIGGERED_BY", "cron")
    with record_run("morning_fade", triggered_by=triggered_by):
        main()
