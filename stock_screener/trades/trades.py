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
              exit_price: Optional[float] = None, notes: str = "",
              setup: str = "manual") -> int:
    """Add a trade. Returns the row ID."""
    side = side.lower()
    if side not in ("long", "short"):
        raise ValueError(f"side must be 'long' or 'short', got {side!r}")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO trades (owner, ticker, side, entry_date, entry_price, shares,
                               exit_date, exit_price, notes, created_at, setup)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (owner, ticker.upper(), side, entry_date, float(entry_price), int(shares),
         exit_date, float(exit_price) if exit_price else None, notes,
         datetime.now().isoformat(), setup.lower()),
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


def update_trade(trade_id: int, **fields) -> None:
    """Patch any subset of trade fields by id. Whitelisted columns only so
    we never accidentally rewrite owner / id / created_at."""
    allowed = {
        "ticker", "side", "entry_date", "entry_price", "shares",
        "exit_date", "exit_price", "notes", "setup",
    }
    sets = []
    vals = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "ticker" and isinstance(v, str):
            v = v.upper()
        if k == "side" and isinstance(v, str):
            v = v.lower()
            if v not in ("long", "short"):
                raise ValueError(f"side must be 'long' or 'short', got {v!r}")
        if k == "setup" and isinstance(v, str):
            v = v.lower()
        if k in ("entry_price", "exit_price") and v is not None:
            v = float(v)
        if k == "shares" and v is not None:
            v = int(v)
        sets.append(f"{k} = ?")
        vals.append(v)
    if not sets:
        return
    vals.append(trade_id)
    conn = get_connection()
    conn.cursor().execute(
        f"UPDATE trades SET {', '.join(sets)} WHERE id = ?",
        tuple(vals),
    )
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


def setup_edge_stats(closed_df: pd.DataFrame) -> pd.DataFrame:
    """Per-setup performance stats over the user's closed trades.

    Returns one row per setup with: trades, win_rate, avg_return_pct,
    profit_factor, avg_hold_days, best_pct, worst_pct. Used by the
    dashboard's 'Edge by Setup' panel — the trader leans into the setups
    that show real edge and prunes the ones that don't.
    """
    if closed_df is None or closed_df.empty:
        return pd.DataFrame()

    df = closed_df.copy()
    if "setup" not in df.columns:
        df["setup"] = "manual"
    df["setup"] = df["setup"].fillna("manual").str.lower()

    # Per-trade return % (signed by side)
    def _ret(row):
        e, x, side = float(row["entry_price"]), float(row["exit_price"]), row["side"]
        return ((x - e) / e * 100) if side == "long" else ((e - x) / e * 100)

    df["ret_pct"] = df.apply(_ret, axis=1)
    df["entry_dt"] = pd.to_datetime(df["entry_date"], errors="coerce")
    df["exit_dt"] = pd.to_datetime(df["exit_date"], errors="coerce")
    df["hold_days"] = (df["exit_dt"] - df["entry_dt"]).dt.days

    out = []
    for setup, g in df.groupby("setup"):
        wins = g[g["ret_pct"] > 0]["ret_pct"]
        losses = g[g["ret_pct"] <= 0]["ret_pct"]
        gross_win = wins.sum()
        gross_loss = abs(losses.sum())
        profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0)
        out.append({
            "setup": setup,
            "trades": len(g),
            "win_rate": (len(wins) / len(g) * 100) if len(g) else 0,
            "avg_return_pct": float(g["ret_pct"].mean()),
            "profit_factor": profit_factor,
            "avg_hold_days": float(g["hold_days"].mean()) if g["hold_days"].notna().any() else 0,
            "best_pct": float(g["ret_pct"].max()),
            "worst_pct": float(g["ret_pct"].min()),
        })
    return pd.DataFrame(out).sort_values("avg_return_pct", ascending=False).reset_index(drop=True)


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


