"""Unit tests for the four scanners.

Strategy: build synthetic OHLCV with known patterns, write to a temp SQLite DB,
then assert each scanner flags / doesn't flag as expected.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))


def _gen_baseline(n_days: int = 260, start_price: float = 100.0, vol: int = 1_000_000,
                  start_date: str = "2025-01-01"):
    """Random-walk OHLCV with deterministic seed."""
    rng = np.random.default_rng(42)
    prices = start_price + np.cumsum(rng.normal(0, 1, n_days))
    prices = np.clip(prices, 5, 1000)
    base = datetime.fromisoformat(start_date)
    rows = []
    for i, close in enumerate(prices):
        d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
        # Avoid weekends for realism (not strictly required)
        rows.append({
            "Date": d,
            "Open": close * (1 + rng.normal(0, 0.005)),
            "High": close * (1 + abs(rng.normal(0, 0.01))),
            "Low": close * (1 - abs(rng.normal(0, 0.01))),
            "Close": close,
            "Volume": vol,
        })
    return pd.DataFrame(rows)


class ScannerTests(unittest.TestCase):
    """Patch get_ohlcv to return our synthetic frames; verify scanner logic."""

    @classmethod
    def setUpClass(cls):
        # Patch has_earnings_within_days globally so the universal filter passes
        cls._earnings_patch = mock.patch(
            "stock_screener.scanners.scanners.has_earnings_within_days",
            return_value=False,
        )
        cls._earnings_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls._earnings_patch.stop()

    def _patch_ohlcv(self, df):
        return mock.patch(
            "stock_screener.scanners.scanners.get_ohlcv",
            return_value=df,
        )

    # ---- Momentum (Runaway Gap) ----

    def test_momentum_fires_on_gap_up_heavy_volume(self):
        from stock_screener.scanners.scanners import scan_runaway_gap
        df = _gen_baseline()
        # Force last bar: gap up, 2x avg volume
        df.iloc[-1, df.columns.get_loc("Open")] = df.iloc[-2]["Close"] * 1.05
        df.iloc[-1, df.columns.get_loc("Close")] = df.iloc[-1]["Open"] * 1.02
        df.iloc[-1, df.columns.get_loc("High")] = df.iloc[-1]["Close"] * 1.01
        df.iloc[-1, df.columns.get_loc("Low")] = df.iloc[-1]["Open"] * 0.99
        df.iloc[-1, df.columns.get_loc("Volume")] = int(df["Volume"].iloc[:-1].mean() * 2)
        with self._patch_ohlcv(df):
            r = scan_runaway_gap("TEST")
        self.assertTrue(r["flagged"], r)

    def test_momentum_quiet_on_gap_with_low_volume(self):
        from stock_screener.scanners.scanners import scan_runaway_gap
        df = _gen_baseline()
        df.iloc[-1, df.columns.get_loc("Open")] = df.iloc[-2]["Close"] * 1.05  # gap up
        df.iloc[-1, df.columns.get_loc("Volume")] = int(df["Volume"].iloc[:-1].mean() * 0.8)  # light
        with self._patch_ohlcv(df):
            r = scan_runaway_gap("TEST")
        self.assertFalse(r["flagged"], r)

    # ---- Fade (Gap Up Normal Volume below MAs) ----

    def test_fade_fires_on_gap_normal_vol_below_mas(self):
        from stock_screener.scanners.scanners import scan_gap_up_normal_volume
        # Build a series where MAs are well above price (sustained downtrend)
        n = 260
        prices = np.linspace(200, 50, n)  # downtrend
        rows = [{
            "Date": (datetime(2025, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d"),
            "Open": p, "High": p * 1.005, "Low": p * 0.995, "Close": p, "Volume": 1_000_000,
        } for i, p in enumerate(prices)]
        df = pd.DataFrame(rows)
        # Last bar: gap up but still well below the moving averages
        df.iloc[-1, df.columns.get_loc("Open")] = df.iloc[-2]["Close"] * 1.03
        df.iloc[-1, df.columns.get_loc("Volume")] = 800_000  # normal/low
        with self._patch_ohlcv(df):
            r = scan_gap_up_normal_volume("TEST")
        self.assertTrue(r["flagged"], r)

    def test_fade_quiet_on_heavy_volume_gap(self):
        from stock_screener.scanners.scanners import scan_gap_up_normal_volume
        n = 260
        prices = np.linspace(200, 50, n)
        rows = [{
            "Date": (datetime(2025, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d"),
            "Open": p, "High": p * 1.005, "Low": p * 0.995, "Close": p, "Volume": 1_000_000,
        } for i, p in enumerate(prices)]
        df = pd.DataFrame(rows)
        df.iloc[-1, df.columns.get_loc("Open")] = df.iloc[-2]["Close"] * 1.03
        df.iloc[-1, df.columns.get_loc("Volume")] = 5_000_000  # heavy → should NOT flag fade
        with self._patch_ohlcv(df):
            r = scan_gap_up_normal_volume("TEST")
        self.assertFalse(r["flagged"], r)

    # ---- Reversal (Bullish Divergence) ----

    def test_reversal_quiet_when_no_new_low(self):
        from stock_screener.scanners.scanners import scan_bullish_divergence
        df = _gen_baseline(n_days=260)
        # Force last bar high above the running min
        df.iloc[-1, df.columns.get_loc("Low")] = df["Low"].iloc[:-1].min() * 1.5
        with self._patch_ohlcv(df):
            r = scan_bullish_divergence("TEST")
        self.assertFalse(r["flagged"], r)

    # ---- Caution (Bearish Divergence) ----

    def test_caution_quiet_when_no_new_high(self):
        from stock_screener.scanners.scanners import scan_bearish_divergence
        df = _gen_baseline(n_days=260)
        df.iloc[-1, df.columns.get_loc("High")] = df["High"].iloc[:-1].max() * 0.5
        with self._patch_ohlcv(df):
            r = scan_bearish_divergence("TEST")
        self.assertFalse(r["flagged"], r)


if __name__ == "__main__":
    unittest.main()
