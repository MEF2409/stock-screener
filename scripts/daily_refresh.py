#!/usr/bin/env python3
"""
Daily refresh job: update universe, fetch data, run scanners, export results.

Run this daily after market close (e.g., 4pm ET). Set up via cron or task scheduler.

Example cron entry (daily at 4:15pm ET):
  15 16 * * Mon-Fri /path/to/.venv/bin/python /path/to/scripts/daily_refresh.py

"""

import sys
from pathlib import Path
from datetime import datetime
import json
import csv

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from stock_screener.data.db import init_db, get_connection
from stock_screener.data.bulk_refresh import refresh_all_ohlcv
from stock_screener.universe.builder import get_universe, update_universe
from stock_screener.scanners.scanners import run_all_scanners
from stock_screener.earnings.earnings import update_earnings_calendar
from stock_screener.alerts.notifier import send_all
from stock_screener.jobs import record_run


def write_scan_history(results: dict) -> None:
    """Snapshot today's flagged tickers into scan_history for trend charts."""
    conn = get_connection()
    cur = conn.cursor()
    today = datetime.now().date().isoformat()
    name_map = {
        "runaway_gap": "Momentum",
        "bullish_div": "Reversal",
        "bearish_div": "Caution",
        "gap_up_normal_vol": "Fade",
    }
    # Replace today's entries (rerun-safe)
    cur.execute("DELETE FROM scan_history WHERE run_date = ?", (today,))
    for scanner_key, label in name_map.items():
        for flag in results.get(scanner_key, []):
            cur.execute(
                "INSERT OR REPLACE INTO scan_history (run_date, scanner, ticker) VALUES (?, ?, ?)",
                (today, label, flag["ticker"]),
            )
    conn.commit()
    conn.close()


def export_results_to_json(results: dict, output_dir: Path) -> Path:
    """Export scanner results to JSON file."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = output_dir / f"results_{timestamp}.json"

    # Filter out non-essential fields from each flag
    cleaned_results = {}
    for scanner_key, flags in results.items():
        cleaned_results[scanner_key] = [
            {k: v for k, v in flag.items() if k in ["ticker", "date", "reason"]}
            for flag in flags
        ]

    with open(filename, "w") as f:
        json.dump(cleaned_results, f, indent=2)

    return filename


def export_results_to_csv(results: dict, output_dir: Path) -> list:
    """Export scanner results to separate CSV files (one per scanner)."""
    filenames = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    scanner_names = {
        "runaway_gap": "Runaway Gap",
        "bullish_div": "Bullish Divergence",
        "bearish_div": "Bearish Divergence",
        "gap_up_normal_vol": "Gap Up Normal Volume",
    }

    for scanner_key, scanner_name in scanner_names.items():
        flags = results[scanner_key]
        if not flags:
            continue

        filename = output_dir / f"{scanner_name.lower().replace(' ', '_')}_{timestamp}.csv"

        # Collect all keys from all flags
        all_keys = set()
        for flag in flags:
            all_keys.update(flag.keys())

        all_keys = sorted(all_keys)

        with open(filename, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=all_keys)
            writer.writeheader()
            for flag in flags:
                writer.writerow(flag)

        filenames.append(filename)

    return filenames


def main():
    print("=" * 60)
    print("Stock Screener — Daily Refresh Job")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Setup
    init_db()
    output_dir = Path(__file__).parent.parent / "results"
    output_dir.mkdir(exist_ok=True)

    # Step 1: Update universe
    print("\n1. Updating universe...")
    try:
        update_universe()
        universe = get_universe()
        print(f"   ✓ Universe: {len(universe)} stocks")
    except Exception as e:
        print(f"   ✗ Error: {e}")
        return

    # Step 2: Refresh OHLCV data
    print("\n2. Refreshing OHLCV data...")
    try:
        refresh_all_ohlcv(days_back=365)
        print("   ✓ Data refreshed")
    except Exception as e:
        print(f"   ✗ Error: {e}")
        return

    # Step 3: Run scanners
    print("\n3. Running scanners...")
    try:
        tickers = universe["ticker"].tolist()
        results = run_all_scanners(tickers)

        total_flags = sum(len(flags) for flags in results.values())
        print(f"   ✓ Total flags: {total_flags}")

        for scanner_key, flags in results.items():
            scanner_name = {
                "runaway_gap": "Runaway Gap",
                "bullish_div": "Bullish Divergence",
                "bearish_div": "Bearish Divergence",
                "gap_up_normal_vol": "Gap Up Normal Volume",
            }[scanner_key]
            print(f"     - {scanner_name}: {len(flags)} flags")

    except Exception as e:
        print(f"   ✗ Error: {e}")
        return

    # Step 4: Export results
    print("\n4. Exporting results...")
    try:
        json_file = export_results_to_json(results, output_dir)
        print(f"   ✓ JSON: {json_file}")

        csv_files = export_results_to_csv(results, output_dir)
        for csv_file in csv_files:
            print(f"   ✓ CSV: {csv_file}")

    except Exception as e:
        print(f"   ✗ Error: {e}")
        return

    # Step 5: Snapshot scan history (for time-series charts)
    print("\n5. Writing scan history snapshot...")
    try:
        write_scan_history(results)
        print("   ✓ scan_history updated")
    except Exception as e:
        print(f"   ✗ Error: {e}")

    # Step 6: Refresh earnings (cheap with Finnhub bulk endpoint)
    print("\n6. Refreshing earnings calendar...")
    try:
        update_earnings_calendar(tickers=universe["ticker"].tolist())
        print("   ✓ Earnings refreshed")
    except Exception as e:
        print(f"   ✗ Error: {e}")

    # Step 6b: Update earnings-reaction stats — only for tickers that recently
    # reported (so we incrementally backfill without hammering Finnhub on
    # every ticker every night).
    print("\n6b. Updating earnings-reaction stats for recent reporters...")
    try:
        from stock_screener.earnings.earnings import update_earnings_reactions
        from stock_screener.data.db import get_connection
        from stock_screener.data.fetcher import get_ohlcv
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT ticker FROM earnings WHERE last_earnings_date >= date('now','-7 days')"
        )
        recent = [r[0] for r in cur.fetchall()]
        conn.close()
        if recent:
            n = update_earnings_reactions(recent, get_ohlcv_fn=get_ohlcv)
            print(f"   ✓ Reactions updated for {n}/{len(recent)} recent reporters")
        else:
            print("   - No recent reporters to refresh")
    except Exception as e:
        print(f"   ✗ Error: {e}")

    # Step 7: Fire alerts (Slack/Discord/email — only those configured)
    print("\n7. Sending alerts...")
    try:
        delivered = send_all(results)
        for channel, ok in delivered.items():
            if ok is None:
                print(f"   - {channel}: not configured")
            elif ok:
                print(f"   ✓ {channel}: sent")
            else:
                print(f"   ✗ {channel}: failed")
    except Exception as e:
        print(f"   ✗ Error: {e}")

    print("\n" + "=" * 60)
    print(f"Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)


if __name__ == "__main__":
    import os
    triggered_by = os.environ.get("JOB_TRIGGERED_BY", "cron")
    with record_run("daily_refresh", triggered_by=triggered_by):
        main()
