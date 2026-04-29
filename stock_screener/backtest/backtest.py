"""Minimal backtest engine: replay scanners on historical dates and compute forward returns.

Caveats (v1):
- Doesn't refit indicators per historical date; uses the indicators as computed today.
  This biases results because RSI/MA on date `D` is approximated from current data.
- Doesn't account for survivorship bias (uses the current universe).
- Doesn't model bid/ask, slippage, transaction costs, or earnings filters historically.
- Forward returns use Close→Close, not entry/exit prices.

It's good enough to spot whether a scanner produces signals that move *roughly* in
the expected direction. It is NOT a research-grade backtester.
"""

from datetime import datetime, timedelta
from typing import Callable

import pandas as pd

from stock_screener.data.fetcher import get_ohlcv
from stock_screener.indicators.indicators import enrich_ohlcv_with_indicators


def _historical_scan_at(ticker: str, df_full: pd.DataFrame, target_date: str,
                        scan_fn_name: str) -> bool:
    """Replay a scanner on a single ticker as of `target_date` using `df_full` truncated."""
    df = df_full[df_full["Date"] <= target_date].copy()
    if len(df) < 50:
        return False
    try:
        df = enrich_ohlcv_with_indicators(df)
        today = df.iloc[-1]
        if scan_fn_name == "Momentum":
            if len(df) < 2: return False
            yest = df.iloc[-2]
            return bool(today["Open"] > yest["Close"]
                        and today["Volume"] >= 1.3 * today["Avg_Volume_30d"])
        if scan_fn_name == "Fade":
            if len(df) < 2: return False
            yest = df.iloc[-2]
            return bool(today["Open"] > yest["Close"]
                        and today["Volume"] < 1.3 * today["Avg_Volume_30d"]
                        and today["Open"] < today["MA_50"]
                        and today["Open"] < today["MA_100"]
                        and today["Open"] < today["MA_200"])
        if scan_fn_name == "Reversal":
            if len(df) < 250: return False
            window = df.tail(250)
            return bool(today["Low"] <= window["Low"].min()
                        and not pd.isna(today["RSI_14"]))
        if scan_fn_name == "Caution":
            if len(df) < 250: return False
            window = df.tail(250)
            return bool(today["High"] >= window["High"].max()
                        and not pd.isna(today["RSI_14"]))
    except Exception:
        return False
    return False


def backtest_scanner(scanner: str, tickers: list[str], start_date: str, end_date: str,
                     forward_days: tuple = (1, 5, 20),
                     progress_callback: Callable | None = None) -> pd.DataFrame:
    """Run `scanner` daily over [start_date, end_date]; return a row per signal with
    forward Close→Close returns at the specified day offsets.

    Args:
        scanner: one of "Momentum", "Reversal", "Caution", "Fade"
        tickers: list of tickers to test
        start_date, end_date: 'YYYY-MM-DD'
        forward_days: tuple of horizons to evaluate
        progress_callback: fn(done:int, total:int, ticker:str)

    Returns DataFrame with columns: date, ticker, scanner, ret_1d, ret_5d, ret_20d, ...
    """
    rows = []
    total = len(tickers)
    for i, ticker in enumerate(tickers):
        if progress_callback:
            try: progress_callback(i + 1, total, ticker)
            except Exception: pass
        try:
            df = get_ohlcv(ticker)
            if df.empty:
                continue
            df = df.sort_values("Date").reset_index(drop=True)
            mask = (df["Date"] >= start_date) & (df["Date"] <= end_date)
            test_dates = df.loc[mask, "Date"].tolist()
            for d in test_dates:
                # Evaluate scanner as-of date d
                if not _historical_scan_at(ticker, df, d, scanner):
                    continue
                # Compute forward returns
                idx = df.index[df["Date"] == d]
                if len(idx) == 0:
                    continue
                i0 = idx[0]
                close0 = float(df.iloc[i0]["Close"])
                row = {"date": d, "ticker": ticker, "scanner": scanner, "entry_close": close0}
                for n in forward_days:
                    j = i0 + n
                    if j < len(df):
                        row[f"ret_{n}d"] = (float(df.iloc[j]["Close"]) / close0 - 1) * 100
                    else:
                        row[f"ret_{n}d"] = None
                rows.append(row)
        except Exception:
            continue
    return pd.DataFrame(rows)


_SETUP_TO_SCANNER = {
    "momentum": "Momentum", "reversal": "Reversal",
    "caution": "Caution", "fade": "Fade",
}
_SETUP_SIDE = {"momentum": "long", "reversal": "long", "caution": "short", "fade": "short"}


def backtest_with_playbook(setup: str, tickers: list[str], start_date: str, end_date: str,
                           max_hold_days: int = 30,
                           progress_callback: Callable | None = None) -> pd.DataFrame:
    """Replay scanner entries AND apply the exit playbook day-by-day.

    For each (ticker, date) the scanner flags, simulate a trade entered at
    that day's close, then walk forward calling `evaluate_exit` until the
    playbook fires action='exit' or max_hold_days is hit. Records the
    realized return so backtest stats reflect the actual trader experience,
    not just fixed-horizon forward returns.

    Returns DataFrame: ticker, setup, side, entry_date, entry_price,
    exit_date, exit_price, exit_reason, hold_days, return_pct, max_dd_pct.
    """
    from stock_screener.exits import evaluate_exit
    scanner = _SETUP_TO_SCANNER.get(setup.lower())
    side = _SETUP_SIDE.get(setup.lower())
    if not scanner or not side:
        return pd.DataFrame()

    rows = []
    total = len(tickers)
    for i, ticker in enumerate(tickers):
        if progress_callback:
            try: progress_callback(i + 1, total, ticker)
            except Exception: pass
        try:
            df = get_ohlcv(ticker)
            # Need ~1y of history so MA50/100/200 + 52w extrema in the exit
            # playbook produce real values. Without this, backtests on
            # short-history tickers silently exit only on stop/target/time
            # rules and undercount MA-based stop-outs.
            if df.empty or len(df) < 250:
                continue
            df = df.sort_values("Date").reset_index(drop=True)
            mask = (df["Date"] >= start_date) & (df["Date"] <= end_date)
            test_dates = df.loc[mask, "Date"].tolist()
            for d in test_dates:
                if not _historical_scan_at(ticker, df, d, scanner):
                    continue
                idx_list = df.index[df["Date"] == d].tolist()
                if not idx_list:
                    continue
                signal_idx = idx_list[0]
                # Realistic fill: the scanner needs day D's full bar to
                # flag (Open + Volume + MAs computed on D's close). A live
                # trader can't get that and act on D's close — they get the
                # signal at EOD and enter at D+1's open.
                entry_idx = signal_idx + 1
                if entry_idx >= len(df):
                    continue
                entry_price = float(df.iloc[entry_idx]["Open"])
                trade = {
                    "ticker": ticker, "side": side, "setup": setup.lower(),
                    "entry_date": df.iloc[entry_idx]["Date"],
                    "entry_price": entry_price, "shares": 1,
                }

                exit_idx = None
                exit_reason = "max_hold"
                # Track max-adverse-excursion using High/Low (intraday extreme),
                # not Close — Close-only undercounts drawdown and misses
                # whether stops were hit intraday.
                if side == "long":
                    running_extreme = float(df.iloc[entry_idx]["Low"])
                else:
                    running_extreme = float(df.iloc[entry_idx]["High"])

                for offset in range(1, max_hold_days + 1):
                    j = entry_idx + offset
                    if j >= len(df):
                        break
                    bar = df.iloc[j]
                    if side == "long":
                        running_extreme = min(running_extreme, float(bar["Low"]))
                    else:
                        running_extreme = max(running_extreme, float(bar["High"]))
                    snapshot = df.iloc[: j + 1].copy()
                    verdict = evaluate_exit(trade, snapshot)
                    if verdict.action == "exit":
                        exit_idx = j
                        exit_reason = (verdict.rules_fired[0] if verdict.rules_fired
                                       else "playbook_exit")
                        break

                if exit_idx is None:
                    exit_idx = min(entry_idx + max_hold_days, len(df) - 1)

                exit_price = float(df.iloc[exit_idx]["Close"])
                exit_date = df.iloc[exit_idx]["Date"]
                hold_days = exit_idx - entry_idx
                if side == "long":
                    ret_pct = (exit_price - entry_price) / entry_price * 100
                    max_dd = (running_extreme - entry_price) / entry_price * 100
                else:
                    ret_pct = (entry_price - exit_price) / entry_price * 100
                    max_dd = (entry_price - running_extreme) / entry_price * 100

                rows.append({
                    "ticker": ticker, "setup": setup.lower(), "side": side,
                    "entry_date": trade["entry_date"], "entry_price": entry_price,
                    "exit_date": exit_date, "exit_price": exit_price,
                    "exit_reason": exit_reason[:80],
                    "hold_days": hold_days,
                    "return_pct": ret_pct,
                    "max_dd_pct": max_dd,
                })
        except Exception:
            continue
    return pd.DataFrame(rows)


def summarize_playbook(results: pd.DataFrame) -> dict:
    """Aggregate stats from a playbook backtest: trades, win rate, profit
    factor, avg return, avg hold, expectancy."""
    if results.empty:
        return {"trades": 0}
    wins = results[results["return_pct"] > 0]["return_pct"]
    losses = results[results["return_pct"] <= 0]["return_pct"]
    gross_w = float(wins.sum())
    gross_l = float(abs(losses.sum()))
    pf = (gross_w / gross_l) if gross_l > 0 else (float("inf") if gross_w > 0 else 0)
    return {
        "trades": int(len(results)),
        "win_rate": float((results["return_pct"] > 0).mean() * 100),
        "avg_return": float(results["return_pct"].mean()),
        "median_return": float(results["return_pct"].median()),
        "profit_factor": pf,
        "avg_hold_days": float(results["hold_days"].mean()),
        "best": float(results["return_pct"].max()),
        "worst": float(results["return_pct"].min()),
        "avg_max_dd": float(results["max_dd_pct"].mean()),
    }


def summarize_results(results: pd.DataFrame, forward_days: tuple = (1, 5, 20)) -> dict:
    """Roll-up stats: hit rate (% positive), mean / median return, count, by horizon."""
    if results.empty:
        return {"count": 0}
    summary = {"count": len(results)}
    for n in forward_days:
        col = f"ret_{n}d"
        if col not in results.columns:
            continue
        ser = results[col].dropna()
        if ser.empty:
            continue
        summary[col] = {
            "n": int(len(ser)),
            "hit_rate": float((ser > 0).mean() * 100),
            "mean": float(ser.mean()),
            "median": float(ser.median()),
            "std": float(ser.std()),
        }
    return summary
