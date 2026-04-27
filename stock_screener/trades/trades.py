"""Trade journal: log entries, track live P&L, grade closed trades.

Grading rationale:
- For LONG trades: best possible exit = max(High) between entry and (exit_date + 30d).
  Capture % = (your exit P&L) / (best possible P&L).
- For SHORT trades: best possible cover = min(Low) in the same window.
- Grades: A ≥ 90%, B ≥ 70%, C ≥ 50%, D ≥ 25%, F < 25%.

Caveats:
- Uses Close-to-Close after exit (no slippage modeling).
- "Best" is hindsight in a 30-day window; real-time exits can't see the future.
"""

from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from stock_screener.data.db import get_connection
from stock_screener.data.fetcher import get_ohlcv


def add_trade(owner: str, ticker: str, side: str, entry_date: str,
              entry_price: float, shares: int, exit_date: Optional[str] = None,
              exit_price: Optional[float] = None, notes: str = "") -> int:
    """Add a trade. Returns the row ID."""
    side = side.lower()
    if side not in ("long", "short"):
        raise ValueError(f"side must be 'long' or 'short', got {side!r}")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO trades (owner, ticker, side, entry_date, entry_price, shares,
                               exit_date, exit_price, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (owner, ticker.upper(), side, entry_date, float(entry_price), int(shares),
         exit_date, float(exit_price) if exit_price else None, notes,
         datetime.now().isoformat()),
    )
    trade_id = cur.lastrowid
    conn.commit()
    conn.close()
    return trade_id


def close_trade(trade_id: int, exit_date: str, exit_price: float, notes: str = "") -> None:
    conn = get_connection()
    cur = conn.cursor()
    if notes:
        cur.execute(
            "UPDATE trades SET exit_date=?, exit_price=?, notes=COALESCE(notes,'') || ?  WHERE id=?",
            (exit_date, float(exit_price), f"\n{notes}", trade_id),
        )
    else:
        cur.execute(
            "UPDATE trades SET exit_date=?, exit_price=? WHERE id=?",
            (exit_date, float(exit_price), trade_id),
        )
    conn.commit()
    conn.close()


def delete_trade(trade_id: int) -> None:
    conn = get_connection()
    conn.cursor().execute("DELETE FROM trades WHERE id = ?", (trade_id,))
    conn.commit()
    conn.close()


def list_trades(owner: str, status: str = "all") -> pd.DataFrame:
    """status: 'open', 'closed', or 'all'."""
    conn = get_connection()
    if status == "open":
        sql = "SELECT * FROM trades WHERE owner=? AND exit_date IS NULL ORDER BY entry_date DESC"
    elif status == "closed":
        sql = "SELECT * FROM trades WHERE owner=? AND exit_date IS NOT NULL ORDER BY exit_date DESC"
    else:
        sql = "SELECT * FROM trades WHERE owner=? ORDER BY entry_date DESC"
    df = pd.read_sql_query(sql, conn, params=(owner,))
    conn.close()
    return df


def compute_pnl(trade: dict, current_price: Optional[float] = None) -> dict:
    """Compute realized (if closed) or unrealized P&L for a trade."""
    entry = float(trade["entry_price"])
    shares = int(trade["shares"])
    side = trade["side"]
    exit_p = trade.get("exit_price")
    is_open = exit_p is None or pd.isna(exit_p)
    mark = exit_p if not is_open else current_price

    if mark is None:
        return {"pnl": None, "pct": None, "is_open": is_open}

    if side == "long":
        pnl = (mark - entry) * shares
    else:
        pnl = (entry - mark) * shares
    pct = ((mark - entry) / entry * 100) if side == "long" else ((entry - mark) / entry * 100)
    return {"pnl": pnl, "pct": pct, "is_open": is_open, "mark": mark}


def _letter_grade(capture_pct: float) -> str:
    if capture_pct >= 90: return "A"
    if capture_pct >= 70: return "B"
    if capture_pct >= 50: return "C"
    if capture_pct >= 25: return "D"
    return "F"


def grade_closed_trade(trade: dict, lookahead_days: int = 30) -> Optional[dict]:
    """For a closed trade, evaluate what the best post-exit price would have been.
    Returns {grade, capture_pct, best_price, best_date, missed_dollars, msg}."""
    if not trade.get("exit_date") or trade.get("exit_price") is None:
        return None
    try:
        df = get_ohlcv(trade["ticker"])
        if df.empty:
            return None
        df = df.sort_values("Date").reset_index(drop=True)
        exit_date = pd.to_datetime(trade["exit_date"]).strftime("%Y-%m-%d")
        end_date = (pd.to_datetime(exit_date) + timedelta(days=lookahead_days)).strftime("%Y-%m-%d")
        window = df[(df["Date"] > exit_date) & (df["Date"] <= end_date)]
        if window.empty:
            return None

        entry = float(trade["entry_price"])
        exit_p = float(trade["exit_price"])
        shares = int(trade["shares"])
        side = trade["side"]

        if side == "long":
            best_idx = window["High"].idxmax()
            best_price = float(window.loc[best_idx, "High"])
            best_date = window.loc[best_idx, "Date"]
            captured = (exit_p - entry) * shares
            best_possible = (best_price - entry) * shares
        else:
            best_idx = window["Low"].idxmin()
            best_price = float(window.loc[best_idx, "Low"])
            best_date = window.loc[best_idx, "Date"]
            captured = (entry - exit_p) * shares
            best_possible = (entry - best_price) * shares

        if best_possible <= 0:
            # The trade was negative even at "best" — wrong direction
            capture_pct = 0.0 if captured < 0 else 100.0
            missed = best_possible - captured
        else:
            capture_pct = max(0.0, min(100.0, (captured / best_possible) * 100))
            missed = best_possible - captured

        verdict = (
            f"Best price reached ${best_price:.2f} on {best_date} "
            f"({lookahead_days}d window). You captured {capture_pct:.0f}% — "
            f"left ${missed:,.2f} on the table."
        ) if missed > 0 else (
            f"Optimal exit. You took ${captured:,.2f} of ${best_possible:,.2f} possible."
        )

        return {
            "grade": _letter_grade(capture_pct),
            "capture_pct": capture_pct,
            "best_price": best_price,
            "best_date": best_date,
            "captured_dollars": captured,
            "best_possible_dollars": best_possible,
            "missed_dollars": missed,
            "msg": verdict,
        }
    except Exception as e:
        return {"error": str(e)}


def suggest_exit(trade: dict, df: pd.DataFrame) -> list[str]:
    """Heuristic exit signals for an OPEN trade. Returns list of human-readable hints."""
    if df.empty:
        return []
    from stock_screener.indicators.indicators import enrich_ohlcv_with_indicators
    df = enrich_ohlcv_with_indicators(df)
    latest = df.iloc[-1]
    rsi = latest.get("RSI_14")
    close = latest.get("Close")
    ma50 = latest.get("MA_50")
    ma200 = latest.get("MA_200")
    side = trade["side"]
    entry = float(trade["entry_price"])

    hints = []
    if pd.notna(rsi):
        if side == "long" and rsi >= 70:
            hints.append(f"RSI {rsi:.0f} — overbought, consider taking profits")
        if side == "short" and rsi <= 30:
            hints.append(f"RSI {rsi:.0f} — oversold, consider covering")

    if pd.notna(close) and pd.notna(ma50):
        if side == "long" and close < ma50:
            hints.append("Price broke below the 50d MA — momentum weakening")
        if side == "short" and close > ma50:
            hints.append("Price broke above the 50d MA — short thesis weakening")

    # Profit / loss thresholds
    pnl_pct = ((close - entry) / entry * 100) if side == "long" else ((entry - close) / entry * 100)
    if pnl_pct >= 15:
        hints.append(f"Up {pnl_pct:.1f}% — consider scaling out / trailing stop")
    if pnl_pct <= -8:
        hints.append(f"Down {abs(pnl_pct):.1f}% — review thesis / consider stop")

    # 200d MA as longer-term trend filter
    if pd.notna(ma200):
        if side == "long" and close < ma200:
            hints.append("Below the 200d MA — long-term trend has flipped against you")
        if side == "short" and close > ma200:
            hints.append("Above the 200d MA — long-term trend against the short")

    return hints
