"""The four stock scanners: Runaway Gap, Bullish/Bearish Divergence, Gap Up Normal Volume."""

import pandas as pd

from stock_screener.data.db import get_connection
from stock_screener.data.fetcher import get_ohlcv
from stock_screener.indicators.indicators import (
    enrich_ohlcv_with_indicators,
    get_52_week_high_low,
    get_previous_52_week_extremum,
)
from stock_screener.earnings.earnings import (
    has_earnings_within_days,
    had_earnings_within_past_days,
)


def _catalyst_for(ticker: str) -> str:
    """Tag a flagged signal: 'earnings' if the company reported in the last
    2 sessions (gap is the post-print reaction), else 'none'."""
    return "earnings" if had_earnings_within_past_days(ticker, days=2) else "none"


def _avg_dollar_volume(price: float, avg_vol_30d: float) -> float | None:
    """Average daily $-volume — proxy for liquidity. Below ~$5M is hard to
    enter/exit cleanly, especially for shorts (HTB locate is also rough)."""
    if price is None or avg_vol_30d is None:
        return None
    try:
        return float(price) * float(avg_vol_30d)
    except Exception:
        return None


def _apply_universal_filters(ticker: str, is_flagged: bool) -> bool:
    """
    Apply universal filters to a flagged stock.

    Returns False if stock should be excluded (e.g., earnings within 14 days).
    """
    if not is_flagged:
        return False

    # Check for upcoming earnings (within 14 days)
    if has_earnings_within_days(ticker, days=14):
        return False

    return True


def scan_runaway_gap(ticker: str) -> dict:
    """
    Bull #1 — Runaway Gap

    Flag if:
    - Today's open > yesterday's close (gap up)
    - Today's volume >= 1.3 × 30-day average daily volume
    """
    try:
        df = get_ohlcv(ticker)
        if len(df) < 2:
            return {"ticker": ticker, "flagged": False, "reason": "Insufficient data"}

        df = enrich_ohlcv_with_indicators(df)

        today = df.iloc[-1]
        yesterday = df.iloc[-2]

        gap_up = today["Open"] > yesterday["Close"]
        high_volume = today["Volume"] >= 1.3 * today["Avg_Volume_30d"]

        flagged = gap_up and high_volume

        if not _apply_universal_filters(ticker, flagged):
            return {"ticker": ticker, "flagged": False, "reason": "Filtered (earnings)"}

        return {
            "ticker": ticker,
            "flagged": flagged,
            "reason": "Runaway Gap" if flagged else "No gap or normal volume",
            "date": today["Date"],
            "open": today["Open"],
            # Prior close (gap reference). Same convention as Fade so alerts
            # can compute (open - close) / close and get the actual gap %.
            "close": yesterday["Close"],
            "volume": today["Volume"],
            "avg_volume_30d": today["Avg_Volume_30d"],
            "avg_dollar_vol": _avg_dollar_volume(today["Close"], today["Avg_Volume_30d"]),
            "catalyst": _catalyst_for(ticker),
        }
    except Exception as e:
        return {"ticker": ticker, "flagged": False, "reason": f"Error: {str(e)}"}


def scan_bullish_divergence(ticker: str) -> dict:
    """
    Bull #2 — Bullish Divergence

    Flag if:
    - Today's price is at a new 52-week low
    - Today's 14-day RSI > RSI on the date of the previous 52-week low
    """
    try:
        df = get_ohlcv(ticker)
        if len(df) < 250:  # Need ~1 year for 52-week data
            return {"ticker": ticker, "flagged": False, "reason": "Insufficient data"}

        df = enrich_ohlcv_with_indicators(df)

        today = df.iloc[-1]
        today_low = today["Low"]
        today_rsi = today["RSI_14"]

        # Get 52-week low
        _, low_price, _, low_date, _, low_rsi = get_52_week_high_low(df, lookback_days=250)

        if low_price is None or pd.isna(today_rsi) or pd.isna(low_rsi):
            return {"ticker": ticker, "flagged": False, "reason": "Missing indicator data"}

        # Is today at a new 52-week low?
        is_new_low = today_low <= low_price

        if not is_new_low:
            return {"ticker": ticker, "flagged": False, "reason": "Not at 52-week low"}

        # Is today's RSI > RSI at previous low?
        prev_low_price, _, prev_low_rsi = get_previous_52_week_extremum(
            df, today["Date"], is_high=False, lookback_days=250
        )

        if prev_low_price is None or pd.isna(prev_low_rsi):
            return {"ticker": ticker, "flagged": False, "reason": "No previous low to compare"}

        higher_rsi = today_rsi > prev_low_rsi

        flagged = is_new_low and higher_rsi

        if not _apply_universal_filters(ticker, flagged):
            return {"ticker": ticker, "flagged": False, "reason": "Filtered (earnings)"}

        return {
            "ticker": ticker,
            "flagged": flagged,
            "reason": "Bullish Divergence" if flagged else "No divergence signal",
            "date": today["Date"],
            "low": today_low,
            "rsi": today_rsi,
            "prev_low": prev_low_price,
            "prev_low_rsi": prev_low_rsi,
            "avg_dollar_vol": _avg_dollar_volume(today["Close"], today["Avg_Volume_30d"]),
            "catalyst": _catalyst_for(ticker),
        }
    except Exception as e:
        return {"ticker": ticker, "flagged": False, "reason": f"Error: {str(e)}"}


def scan_bearish_divergence(ticker: str) -> dict:
    """
    Bear #1 — Bearish Divergence

    Flag if:
    - Today's price is at a new 52-week high
    - Today's 14-day RSI < RSI on the date of the previous 52-week high
    """
    try:
        df = get_ohlcv(ticker)
        if len(df) < 250:
            return {"ticker": ticker, "flagged": False, "reason": "Insufficient data"}

        df = enrich_ohlcv_with_indicators(df)

        today = df.iloc[-1]
        today_high = today["High"]
        today_rsi = today["RSI_14"]

        # Get 52-week high
        high_price, _, high_date, _, high_rsi, _ = get_52_week_high_low(df, lookback_days=250)

        if high_price is None or pd.isna(today_rsi) or pd.isna(high_rsi):
            return {"ticker": ticker, "flagged": False, "reason": "Missing indicator data"}

        # Is today at a new 52-week high?
        is_new_high = today_high >= high_price

        if not is_new_high:
            return {"ticker": ticker, "flagged": False, "reason": "Not at 52-week high"}

        # Is today's RSI < RSI at previous high?
        prev_high_price, _, prev_high_rsi = get_previous_52_week_extremum(
            df, today["Date"], is_high=True, lookback_days=250
        )

        if prev_high_price is None or pd.isna(prev_high_rsi):
            return {"ticker": ticker, "flagged": False, "reason": "No previous high to compare"}

        lower_rsi = today_rsi < prev_high_rsi

        flagged = is_new_high and lower_rsi

        if not _apply_universal_filters(ticker, flagged):
            return {"ticker": ticker, "flagged": False, "reason": "Filtered (earnings)"}

        return {
            "ticker": ticker,
            "flagged": flagged,
            "reason": "Bearish Divergence" if flagged else "No divergence signal",
            "date": today["Date"],
            "high": today_high,
            "rsi": today_rsi,
            "prev_high": prev_high_price,
            "prev_high_rsi": prev_high_rsi,
            "avg_dollar_vol": _avg_dollar_volume(today["Close"], today["Avg_Volume_30d"]),
            "catalyst": _catalyst_for(ticker),
        }
    except Exception as e:
        return {"ticker": ticker, "flagged": False, "reason": f"Error: {str(e)}"}


def scan_gap_up_normal_volume(ticker: str) -> dict:
    """
    Bear #2 — Gap Up on Normal Volume

    Flag if:
    - Today's open > yesterday's close (gap up)
    - Today's volume is within normal range (NOT >= 1.3 × 30-day avg)
    - Today's open is below the 50-day, 100-day, AND 200-day moving averages
    """
    try:
        df = get_ohlcv(ticker)
        if len(df) < 2:
            return {"ticker": ticker, "flagged": False, "reason": "Insufficient data"}

        df = enrich_ohlcv_with_indicators(df)

        today = df.iloc[-1]
        yesterday = df.iloc[-2]

        gap_up = today["Open"] > yesterday["Close"]
        normal_volume = today["Volume"] < 1.3 * today["Avg_Volume_30d"]

        # Check if open is below all three MAs
        below_all_mas = (
            today["Open"] < today["MA_50"]
            and today["Open"] < today["MA_100"]
            and today["Open"] < today["MA_200"]
        )

        flagged = gap_up and normal_volume and below_all_mas

        if not _apply_universal_filters(ticker, flagged):
            return {"ticker": ticker, "flagged": False, "reason": "Filtered (earnings)"}

        return {
            "ticker": ticker,
            "flagged": flagged,
            "reason": "Gap Up Normal Volume (short candidate)" if flagged else "No short setup",
            "date": today["Date"],
            "open": today["Open"],
            # `close` here is the *prior session's* close — the gap-up reference.
            # During the morning Fade run today["Close"] is just an intraday
            # tick that moves with every fetch and doesn't tell the user
            # anything about the gap. Showing yesterday's close lets the user
            # compute the gap directly: today's open vs. this number.
            "close": yesterday["Close"],
            "volume": today["Volume"],
            "avg_volume_30d": today["Avg_Volume_30d"],
            "avg_dollar_vol": _avg_dollar_volume(today["Close"], today["Avg_Volume_30d"]),
            "ma_50": today["MA_50"],
            "ma_100": today["MA_100"],
            "ma_200": today["MA_200"],
            "catalyst": _catalyst_for(ticker),
        }
    except Exception as e:
        return {"ticker": ticker, "flagged": False, "reason": f"Error: {str(e)}"}


def run_all_scanners(tickers: list) -> dict:
    """
    Run all four scanners against a list of tickers.

    Returns a dict with keys: runaway_gap, bullish_div, bearish_div, gap_up_normal_vol
    Each contains a list of flagged stocks.
    """
    results = {
        "runaway_gap": [],
        "bullish_div": [],
        "bearish_div": [],
        "gap_up_normal_vol": [],
    }

    for ticker in tickers:
        result = scan_runaway_gap(ticker)
        if result["flagged"]:
            results["runaway_gap"].append(result)

        result = scan_bullish_divergence(ticker)
        if result["flagged"]:
            results["bullish_div"].append(result)

        result = scan_bearish_divergence(ticker)
        if result["flagged"]:
            results["bearish_div"].append(result)

        result = scan_gap_up_normal_volume(ticker)
        if result["flagged"]:
            results["gap_up_normal_vol"].append(result)

    return results
