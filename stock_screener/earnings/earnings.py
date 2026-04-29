"""Fetch and manage earnings calendar for stocks.

Primary source: Finnhub /calendar/earnings (bulk, fast).
Fallback: yfinance.Ticker.info (per-ticker, slow) when no FINNHUB_API_KEY is set
or the Finnhub call fails.
"""

import os
from datetime import datetime, timedelta

import pandas as pd
import requests
import yfinance as yf

from stock_screener.data.db import get_connection

FINNHUB_BASE = "https://finnhub.io/api/v1"
FINNHUB_KEY = os.getenv("FINNHUB_API_KEY")


def _fetch_finnhub_earnings_window(start: str, end: str) -> list[dict]:
    """Fetch earnings calendar from Finnhub for a date window.
    Returns the raw list of {symbol, date, ...} entries."""
    if not FINNHUB_KEY:
        return []
    try:
        resp = requests.get(
            f"{FINNHUB_BASE}/calendar/earnings",
            params={"from": start, "to": end, "token": FINNHUB_KEY},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("earningsCalendar", []) or []
    except Exception:
        return []


def _split_past_future(rows: list[dict], today: str) -> tuple[dict[str, str], dict[str, str]]:
    """Partition Finnhub rows into (most-recent-past per ticker, soonest-future per ticker).

    A report dated TODAY is treated as past — it's a BMO print whose price
    reaction is already in today's open. Otherwise the catalyst lookup
    misses the very case the morning workflow was built for.
    """
    past: dict[str, str] = {}
    future: dict[str, str] = {}
    for row in rows:
        sym = row.get("symbol")
        date = row.get("date")
        if not sym or not date:
            continue
        if date <= today:
            if sym not in past or date > past[sym]:
                past[sym] = date
        else:
            if sym not in future or date < future[sym]:
                future[sym] = date
    return past, future


def fetch_next_earnings_date(ticker: str) -> str | None:
    """Fetch the next earnings date for a stock (single ticker, slow yfinance fallback)."""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        if "earningsDate" in info and info["earningsDate"]:
            earnings_ts = info["earningsDate"][0]
            return datetime.fromtimestamp(earnings_ts).strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


def update_earnings_calendar(tickers: list, lookahead_days: int = 60,
                             lookback_days: int = 14) -> None:
    """
    Fetch and update earnings dates for a list of tickers in SQLite.

    Stores both the next upcoming earnings date and the most recent past
    earnings date (used to tag signals whose gap is a post-print reaction).
    """
    conn = get_connection()
    cursor = conn.cursor()

    today = datetime.now().date()
    today_iso = today.isoformat()
    past_start = (today - timedelta(days=lookback_days)).isoformat()
    past_end = today_iso  # include today so BMO prints land in past
    future_start = (today + timedelta(days=1)).isoformat()
    future_end = (today + timedelta(days=lookahead_days)).isoformat()

    past_dates: dict[str, str] = {}
    future_dates: dict[str, str] = {}
    if FINNHUB_KEY:
        # Two separate queries: Finnhub caps each response at ~1500 rows, so
        # a wide window combining past + future would silently drop one side.
        print(f"Fetching past earnings from Finnhub: {past_start} to {past_end}")
        past_rows = _fetch_finnhub_earnings_window(past_start, past_end)
        past_dates, _ = _split_past_future(past_rows, today_iso)
        print(f"Fetching upcoming earnings from Finnhub: {future_start} to {future_end}")
        future_rows = _fetch_finnhub_earnings_window(future_start, future_end)
        _, future_dates = _split_past_future(future_rows, today_iso)
        print(f"  Finnhub returned {len(past_rows)} past + {len(future_rows)} future rows: "
              f"{len(future_dates)} upcoming, {len(past_dates)} recent")
    else:
        print("FINNHUB_API_KEY not set — falling back to yfinance (slow, upcoming only).")

    print(f"Updating earnings calendar for {len(tickers)} stocks...")

    now = datetime.now().isoformat()
    success_count = 0
    fallback_count = 0
    for i, ticker in enumerate(tickers):
        if (i + 1) % 250 == 0:
            print(f"  Progress: {i + 1}/{len(tickers)}")

        next_date = future_dates.get(ticker)
        last_date = past_dates.get(ticker)
        if not next_date and not FINNHUB_KEY:
            # Finnhub off — yfinance fallback for upcoming only
            next_date = fetch_next_earnings_date(ticker)
            if next_date:
                fallback_count += 1

        try:
            cursor.execute(
                """INSERT OR REPLACE INTO earnings
                   (ticker, next_earnings_date, last_earnings_date, last_updated)
                   VALUES (?, ?, ?, ?)""",
                (ticker, next_date, last_date, now),
            )
            success_count += 1
        except Exception:
            continue

    conn.commit()
    conn.close()
    print(f"Earnings calendar updated: {success_count} stocks ({fallback_count} via yfinance fallback)")


def had_earnings_within_past_days(ticker: str, days: int = 2) -> bool:
    """Did this stock report earnings within the last N calendar days?

    Used to tag a gap as catalyst='earnings'. Default 2 days catches both
    after-hours yesterday and pre-market today.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT last_earnings_date FROM earnings WHERE ticker = ?", (ticker,))
    row = cur.fetchone()
    conn.close()
    if not row or not row[0]:
        return False
    try:
        last = datetime.strptime(row[0], "%Y-%m-%d").date()
    except ValueError:
        return False
    return 0 <= (datetime.now().date() - last).days <= days


def has_earnings_within_days(ticker: str, days: int = 14) -> bool:
    """
    Check if a stock has an earnings announcement within the next N days.

    Args:
        ticker: Stock ticker
        days: Number of days to look ahead (default 14)

    Returns:
        True if earnings within the window, False otherwise
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT next_earnings_date FROM earnings WHERE ticker = ?
    """, (ticker,))

    row = cursor.fetchone()
    conn.close()

    if not row or not row[0]:
        # No earnings date recorded; assume it's safe (not blocking)
        return False

    earnings_date = datetime.strptime(row[0], "%Y-%m-%d").date()
    today = datetime.now().date()
    days_until_earnings = (earnings_date - today).days

    # Strictly future-only: today's BMO print is in the past now (its price
    # reaction is already in today's open). Filter is meant to skip names
    # that haven't reported yet but will soon.
    return 0 < days_until_earnings <= days


def _fetch_finnhub_past_earnings(ticker: str, limit: int = 8) -> list[str]:
    """Per-ticker historical earnings dates via Finnhub /stock/earnings.
    Returns up to `limit` past report dates (most recent first)."""
    if not FINNHUB_KEY:
        return []
    try:
        resp = requests.get(
            f"{FINNHUB_BASE}/stock/earnings",
            params={"symbol": ticker, "limit": limit, "token": FINNHUB_KEY},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json() or []
    except Exception:
        return []
    dates = [row.get("period") for row in data if row.get("period")]
    return [d for d in dates if d]


def compute_earnings_reactions(ticker: str, get_ohlcv_fn) -> dict | None:
    """Compute per-ticker reaction stats: avg gap %, same-day return %,
    5-day return %, and fade-back rate over the last ~8 earnings events.

    Definitions:
    - gap_pct: (open after report - close before report) / close before
    - same_day_pct: (close after report - close before report) / close before
    - 5d_pct: (close 5 days after report - close before report) / close before
    - fade_rate: among events where gap is in one direction, what % of the
      time did same-day close move >50% back toward (or past) the prior close.
    """
    dates = _fetch_finnhub_past_earnings(ticker, limit=8)
    if len(dates) < 3:
        return None
    df = get_ohlcv_fn(ticker)
    if df is None or df.empty:
        return None
    df = df.sort_values("Date").reset_index(drop=True)

    gaps = []
    same_day = []
    five_day = []
    fades = 0
    fade_eligible = 0
    for d in dates:
        # The Finnhub period date is the report fiscal date — we treat the
        # following trading session as the "reaction" bar. Find the first
        # bar with date >= d, then check d's prior close for the gap ref.
        post_idx = df.index[df["Date"] >= d].tolist()
        if not post_idx:
            continue
        post_idx = post_idx[0]
        if post_idx == 0:
            continue
        prior_close = float(df.iloc[post_idx - 1]["Close"])
        if prior_close <= 0:
            continue
        post_open = float(df.iloc[post_idx]["Open"])
        post_close = float(df.iloc[post_idx]["Close"])
        gap = (post_open - prior_close) / prior_close * 100
        sd = (post_close - prior_close) / prior_close * 100
        five_idx = post_idx + 4
        fd = ((float(df.iloc[five_idx]["Close"]) - prior_close) / prior_close * 100
              if five_idx < len(df) else None)
        gaps.append(gap)
        same_day.append(sd)
        if fd is not None:
            five_day.append(fd)
        # Fade detection: gap moved one way, close moved >=50% back
        if abs(gap) >= 1.0:
            fade_eligible += 1
            if (gap > 0 and sd < gap * 0.5) or (gap < 0 and sd > gap * 0.5):
                fades += 1

    if not gaps:
        return None
    return {
        "ticker": ticker,
        "n_events": len(gaps),
        "avg_gap_pct": sum(gaps) / len(gaps),
        "avg_same_day_pct": sum(same_day) / len(same_day),
        "avg_5d_pct": (sum(five_day) / len(five_day)) if five_day else None,
        "fade_rate": (fades / fade_eligible * 100) if fade_eligible else None,
    }


def update_earnings_reactions(tickers: list[str], get_ohlcv_fn) -> int:
    """Compute and store reaction stats for a list of tickers. Returns count
    successfully written. Slow (1 Finnhub req per ticker) — runs nightly."""
    from stock_screener.data.db import get_connection
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.now().isoformat()
    success = 0
    for i, ticker in enumerate(tickers):
        if (i + 1) % 100 == 0:
            print(f"  reactions {i + 1}/{len(tickers)} success={success}")
        stats = compute_earnings_reactions(ticker, get_ohlcv_fn)
        if not stats:
            continue
        cur.execute(
            """INSERT OR REPLACE INTO earnings_reactions
               (ticker, n_events, avg_gap_pct, avg_same_day_pct, avg_5d_pct, fade_rate, last_updated)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (stats["ticker"], stats["n_events"], stats["avg_gap_pct"],
             stats["avg_same_day_pct"], stats["avg_5d_pct"], stats["fade_rate"], now),
        )
        success += 1
    conn.commit()
    conn.close()
    return success


def get_earnings_reaction(ticker: str) -> dict | None:
    """Look up cached reaction stats for a ticker."""
    from stock_screener.data.db import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT ticker, n_events, avg_gap_pct, avg_same_day_pct, avg_5d_pct, fade_rate "
        "FROM earnings_reactions WHERE ticker = ?",
        (ticker,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "ticker": row[0], "n_events": row[1],
        "avg_gap_pct": row[2], "avg_same_day_pct": row[3],
        "avg_5d_pct": row[4], "fade_rate": row[5],
    }


def get_earnings_dates(tickers: list = None) -> pd.DataFrame:
    """
    Retrieve earnings dates for tickers from SQLite.

    Args:
        tickers: Optional list of tickers to filter. If None, returns all.

    Returns:
        DataFrame with columns: ticker, next_earnings_date, last_updated
    """
    conn = get_connection()

    if tickers:
        placeholders = ",".join(["?"] * len(tickers))
        query = f"SELECT * FROM earnings WHERE ticker IN ({placeholders}) ORDER BY ticker"
        df = pd.read_sql_query(query, conn, params=tickers)
    else:
        df = pd.read_sql_query("SELECT * FROM earnings ORDER BY ticker", conn)

    conn.close()
    return df
