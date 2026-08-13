#!/usr/bin/env python3
"""Run the four scanners against existing OHLCV in SQLite.

Skips the two slow steps of daily_refresh (universe rebuild + full
OHLCV pull). Uses whatever data is already in the DB. Useful when:

- The full daily_refresh is hung or hasn't run and you want fresh
  scanner output against yesterday's data
- You want to A/B a scanner change against the current cached data
- You want to answer "are the current signals stale because no fresh
  data pulled, or because nothing qualifies today?"

Completes in seconds — no HTTP calls at all. Reads OHLCV from
daily_ohlcv, runs the four scanners, writes results to scan_history,
exports JSON + CSVs to results/, sends alerts.
"""

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from stock_screener.data.db import init_db, get_connection
from stock_screener.universe.builder import get_universe
from stock_screener.scanners.scanners import run_all_scanners
from stock_screener.alerts.notifier import send_all
from stock_screener.jobs import record_run


_SCANNER_NAMES = {
    "runaway_gap": "Momentum",
    "bullish_div": "Reversal",
    "bearish_div": "Caution",
    "gap_up_normal_vol": "Fade",
}


def _write_scan_history(results: dict) -> None:
    """Snapshot today's flags into scan_history (same format as the
    daily_refresh writer so first-seen lookups keep working)."""
    conn = get_connection()
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    total = 0
    for scanner_key, scanner_name in _SCANNER_NAMES.items():
        for f in results.get(scanner_key, []):
            cursor.execute(
                "INSERT OR IGNORE INTO scan_history(run_date, scanner, ticker) VALUES (?, ?, ?)",
                (today, scanner_name, f["ticker"]),
            )
            total += 1
    conn.commit()
    conn.close()
    print(f"   ✓ scan_history: {total} rows upserted for {today}")


def _export_json(results: dict, output_dir: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"results_{ts}.json"
    with open(path, "w") as f:
        json.dump({"generated_at": ts, "results": results}, f, indent=2, default=str)
    return path


def _export_csv(results: dict, output_dir: Path) -> list[Path]:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    paths: list[Path] = []
    for scanner_key, flags in results.items():
        if not flags:
            continue
        scanner_name = _SCANNER_NAMES.get(scanner_key, scanner_key)
        fname = f"{scanner_name.lower().replace(' ', '_')}_{ts}.csv"
        p = output_dir / fname
        with open(p, "w", newline="") as f:
            fields = sorted({k for flag in flags for k in flag.keys()})
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for row in flags:
                w.writerow(row)
        paths.append(p)
    return paths


def main():
    print("=" * 60)
    print("Stock Screener — Run Scanners (no data refresh)")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    init_db()
    output_dir = Path(__file__).parent.parent / "results"
    output_dir.mkdir(exist_ok=True)

    universe = get_universe()
    if universe.empty:
        print("Universe is empty — run daily_refresh at least once first.")
        return
    tickers = universe["ticker"].tolist()
    print(f"\nRunning against {len(tickers)} tickers of existing OHLCV data...")

    results = run_all_scanners(tickers)

    total = sum(len(f) for f in results.values())
    print(f"\nTotal flags: {total}")
    for key, name in _SCANNER_NAMES.items():
        print(f"  - {name}: {len(results.get(key, []))} flags")

    print("\nWriting scan_history + exporting results...")
    _write_scan_history(results)
    json_path = _export_json(results, output_dir)
    print(f"   ✓ JSON: {json_path}")
    for p in _export_csv(results, output_dir):
        print(f"   ✓ CSV: {p}")

    print("\nSending alerts...")
    try:
        for channel, ok in send_all(results).items():
            marker = "✓" if ok else ("-" if ok is None else "✗")
            print(f"   {marker} {channel}: {'sent' if ok else 'not configured' if ok is None else 'failed'}")
    except Exception as e:
        print(f"   ✗ alerts error: {e}")

    print("\n" + "=" * 60)
    print(f"Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)


if __name__ == "__main__":
    import os
    triggered_by = os.environ.get("JOB_TRIGGERED_BY", "cron")
    with record_run("run_scanners", triggered_by=triggered_by):
        main()
