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
