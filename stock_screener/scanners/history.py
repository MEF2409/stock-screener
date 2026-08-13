"""Read scan_history for cross-day context on today's signals.

The scanners themselves are stateless — they run against today's data
and return whoever qualifies right now. This module reads the
scan_history table (written after every scanner run) to add temporal
context: 'first seen', 'consecutive days', etc.

Currently: `first_seen_dates(scanner_name)` for the 'First Seen'
column in the dashboard signal tables.
"""

from __future__ import annotations

from stock_screener.data.db import get_connection


def first_seen_dates(scanner_name: str) -> dict[str, str]:
    """{ticker: earliest_YYYY-MM-DD} across all recorded history for
    this scanner. Answers 'has this ticker been in the list for days
    or is it new today?' — useful diagnostic when signals look stale
    to figure out whether the scanner really isn't producing new
    tickers or whether the underlying job just hasn't run."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT ticker, MIN(run_date) FROM scan_history "
        "WHERE scanner = ? GROUP BY ticker",
        (scanner_name,),
    ).fetchall()
    conn.close()
    return {ticker: date for ticker, date in rows}
