"""Calculate technical indicators: RSI, moving averages, average volume."""

import pandas as pd
import numpy as np


def calculate_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    """
    Calculate Relative Strength Index (RSI).

    Args:
        prices: Series of close prices
        period: RSI period (default 14)

    Returns:
        Series of RSI values
    """
    delta = prices.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return rsi


def calculate_moving_average(prices: pd.Series, period: int) -> pd.Series:
    """
    Calculate Simple Moving Average.

    Args:
        prices: Series of close prices
        period: MA period

    Returns:
        Series of MA values
    """
    return prices.rolling(window=period).mean()


def calculate_avg_volume(volumes: pd.Series, period: int = 30) -> pd.Series:
    """
    Calculate rolling average volume.

    Args:
        volumes: Series of volume values
        period: Average period (default 30 days)

    Returns:
        Series of average volume values
    """
    return volumes.rolling(window=period).mean()


def enrich_ohlcv_with_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add indicator columns to OHLCV DataFrame.

    Expected input columns: Date, Open, High, Low, Close, Volume
    Added columns: RSI_14, MA_50, MA_100, MA_200, Avg_Volume_30d

    Args:
        df: OHLCV DataFrame

    Returns:
        DataFrame with indicator columns added
    """
    df = df.copy()

    df["RSI_14"] = calculate_rsi(df["Close"], 14)
    df["MA_50"] = calculate_moving_average(df["Close"], 50)
    df["MA_100"] = calculate_moving_average(df["Close"], 100)
    df["MA_200"] = calculate_moving_average(df["Close"], 200)
    df["Avg_Volume_30d"] = calculate_avg_volume(df["Volume"], 30)

    return df


def get_52_week_high_low(df: pd.DataFrame, lookback_days: int = 252) -> tuple:
    """
    Get the 52-week high, low, and their dates from OHLCV data.

    Args:
        df: OHLCV DataFrame sorted by date ascending
        lookback_days: Number of days to look back (default 252 ≈ 52 weeks)

    Returns:
        Tuple of (high_price, low_price, high_date, low_date, high_rsi, low_rsi)
        If insufficient data, returns None values.
    """
    if len(df) < lookback_days:
        return None, None, None, None, None, None

    df_window = df.tail(lookback_days)

    high_idx = df_window["High"].idxmax()
    low_idx = df_window["Low"].idxmin()

    high_price = df_window.loc[high_idx, "High"]
    low_price = df_window.loc[low_idx, "Low"]
    high_date = df_window.loc[high_idx, "Date"]
    low_date = df_window.loc[low_idx, "Date"]

    # RSI at those dates (if available)
    high_rsi = df_window.loc[high_idx, "RSI_14"] if "RSI_14" in df_window.columns else None
    low_rsi = df_window.loc[low_idx, "RSI_14"] if "RSI_14" in df_window.columns else None

    return high_price, low_price, high_date, low_date, high_rsi, low_rsi


def get_previous_52_week_extremum(df: pd.DataFrame, current_date: str, is_high: bool, lookback_days: int = 252) -> tuple:
    """
    Get the previous 52-week high or low (prior to the current date).

    Args:
        df: OHLCV DataFrame sorted by date ascending
        current_date: Current date as string 'YYYY-MM-DD'
        is_high: True for high, False for low
        lookback_days: Number of days to look back

    Returns:
        Tuple of (price, date, rsi)
        If not found, returns (None, None, None).
    """
    # Find current row index
    current_rows = df[df["Date"] == current_date]
    if current_rows.empty:
        return None, None, None

    current_idx = current_rows.index[0]

    # Get window BEFORE current date
    if current_idx < lookback_days:
        window_start = 0
    else:
        window_start = current_idx - lookback_days

    df_window = df.iloc[window_start:current_idx]

    if df_window.empty:
        return None, None, None

    if is_high:
        extremum_idx = df_window["High"].idxmax()
        price = df_window.loc[extremum_idx, "High"]
    else:
        extremum_idx = df_window["Low"].idxmin()
        price = df_window.loc[extremum_idx, "Low"]

    date = df_window.loc[extremum_idx, "Date"]
    rsi = df_window.loc[extremum_idx, "RSI_14"] if "RSI_14" in df_window.columns else None

    return price, date, rsi
