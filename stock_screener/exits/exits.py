"""Rule-based exit advisor.

Each scanner setup has its own thesis, and therefore its own correct way
to exit. A short Fade trade is wrong if the gap fills upward; a long
Momentum trade is wrong if the gap fills downward. One generic exit
function can't handle both. This module routes each open trade to the
playbook for its `setup` tag and returns a structured verdict.

Verdict shape (ExitVerdict): action ('hold'/'trim'/'exit'), confidence
('low'/'medium'/'high'), rules_fired (list of human-readable bullets),
key_levels (named price levels the trader should watch), context (one
line summary). The dashboard renders these inline next to each open trade.

Setups: 'momentum' (Runaway Gap long), 'reversal' (Bullish Divergence
long), 'caution' (Bearish Divergence short), 'fade' (Gap-Up-Normal-Volume
short), 'manual' (no setup tag — generic fallback).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

import pandas as pd

from stock_screener.indicators.indicators import enrich_ohlcv_with_indicators


SETUP_CHOICES = ["momentum", "reversal", "caution", "fade", "manual"]

Action = Literal["hold", "trim", "exit"]
Confidence = Literal["low", "medium", "high"]

_CONF_RANK = {"low": 1, "medium": 2, "high": 3}
_ACTION_RANK = {"hold": 0, "trim": 1, "exit": 2}


@dataclass
class ExitVerdict:
    action: Action = "hold"
    confidence: Confidence = "low"
    rules_fired: list[str] = field(default_factory=list)
    key_levels: dict[str, float] = field(default_factory=dict)
    context: str = ""

    def add(self, action: Action, confidence: Confidence, rule: str) -> None:
        self.rules_fired.append(rule)
        if _ACTION_RANK[action] > _ACTION_RANK[self.action]:
            self.action = action
            self.confidence = confidence
        elif _ACTION_RANK[action] == _ACTION_RANK[self.action]:
            if _CONF_RANK[confidence] > _CONF_RANK[self.confidence]:
                self.confidence = confidence


def _pnl_pct(side: str, entry: float, mark: float) -> float:
    if side == "long":
        return (mark - entry) / entry * 100
    return (entry - mark) / entry * 100


def _days_in_trade(entry_date: str) -> int:
    try:
        d = pd.to_datetime(entry_date).date()
        return (datetime.now().date() - d).days
    except Exception:
        return 0


def _atr(df: pd.DataFrame, n: int = 14) -> Optional[float]:
    if len(df) < n + 1:
        return None
    high = df["High"]
    low = df["Low"]
    prev_close = df["Close"].shift(1)
    tr = pd.concat([(high - low),
                    (high - prev_close).abs(),
                    (low - prev_close).abs()], axis=1).max(axis=1)
    return float(tr.tail(n).mean())


def _evaluate_fade(trade: dict, df: pd.DataFrame, v: ExitVerdict) -> None:
    """Short — gap up on light volume below MAs. Cover on gap fill,
    momentum reversal, or after the catalyst stales out."""
    latest = df.iloc[-1]
    entry = float(trade["entry_price"])
    mark = float(latest["Close"])
    pnl = _pnl_pct("short", entry, mark)
    days = _days_in_trade(trade["entry_date"])

    # Estimated cover targets / stops
    atr = _atr(df)
    stop_above = entry * 1.02 if atr is None else entry + 1.5 * atr
    target_below = entry * 0.95 if atr is None else entry - 2.0 * atr
    v.key_levels["entry"] = entry
    v.key_levels["stop"] = stop_above
    v.key_levels["target"] = target_below

    # Profit-side rules
    if pnl >= 5:
        v.add("exit", "high", f"Down {pnl:.1f}% — at full target, lock it in")
    elif pnl >= 3:
        v.add("trim", "medium", f"Down {pnl:.1f}% — partial cover, raise stop to entry")

    # Stop-side rules
    if mark >= stop_above:
        v.add("exit", "high", f"Mark ${mark:.2f} broke above stop ${stop_above:.2f} — fade failed")
    elif pnl <= -2:
        v.add("trim", "medium", f"Up {abs(pnl):.1f}% against you — tighten stop, halve size")

    # Trend/structure: closing back above MA50 means thesis (below MAs) is broken
    ma50 = latest.get("MA_50")
    if pd.notna(ma50) and mark > ma50:
        v.add("exit", "high", f"Closed above 50d MA (${ma50:.2f}) — short thesis broken")

    # Time stop: Fade signals are catalyst-driven and decay quickly
    if days >= 3 and abs(pnl) < 1.5:
        v.add("exit", "medium", f"{days}d in trade with no follow-through — catalyst stale, free up capital")

    # Oversold cover into weakness
    rsi = latest.get("RSI_14")
    if pd.notna(rsi) and rsi <= 30 and pnl > 0:
        v.add("trim", "medium", f"RSI {rsi:.0f} — short-term oversold, take some profit")


def _evaluate_momentum(trade: dict, df: pd.DataFrame, v: ExitVerdict) -> None:
    """Long — Runaway Gap on heavy volume. Trail aggressively, cut on gap fill."""
    latest = df.iloc[-1]
    entry = float(trade["entry_price"])
    mark = float(latest["Close"])
    pnl = _pnl_pct("long", entry, mark)
    days = _days_in_trade(trade["entry_date"])

    atr = _atr(df)
    stop_below = entry * 0.96 if atr is None else entry - 1.5 * atr
    v.key_levels["entry"] = entry
    v.key_levels["stop"] = stop_below

    # Gap-fill stop: if price breaks the entry-day low, the demand was a head-fake
    entry_day = df[df["Date"] <= trade["entry_date"]].tail(1)
    if not entry_day.empty:
        gap_floor = float(entry_day.iloc[0]["Low"])
        v.key_levels["gap_floor"] = gap_floor
        if mark < gap_floor:
            v.add("exit", "high",
                  f"Mark ${mark:.2f} broke entry-day low ${gap_floor:.2f} — gap filled, momentum failed")

    # Stop violation
    if mark < stop_below:
        v.add("exit", "high", f"Stop ${stop_below:.2f} hit — cut")

    # Trend break — closing below MA50 invalidates a runaway-gap thesis
    ma50 = latest.get("MA_50")
    if pd.notna(ma50) and mark < ma50 and days >= 1:
        v.add("exit", "medium", f"Closed below 50d MA (${ma50:.2f}) — momentum thesis weakening")

    # Profit scaling
    if pnl >= 15:
        v.add("trim", "medium", f"Up {pnl:.1f}% — scale out a third, trail the rest")
    elif pnl >= 8:
        v.add("trim", "low", f"Up {pnl:.1f}% — consider raising stop to breakeven")
        v.key_levels["raise_stop_to"] = entry

    # Overbought + bearish daily candle = exhaustion
    rsi = latest.get("RSI_14")
    bearish_day = float(latest["Close"]) < float(latest["Open"])
    if pd.notna(rsi) and rsi >= 80 and bearish_day:
        v.add("trim", "medium", f"RSI {rsi:.0f} + red daily candle — momentum exhausting")


def _evaluate_reversal(trade: dict, df: pd.DataFrame, v: ExitVerdict) -> None:
    """Long — Bullish Divergence (new 52w low, higher RSI). Bottom-fishing."""
    latest = df.iloc[-1]
    entry = float(trade["entry_price"])
    mark = float(latest["Close"])
    pnl = _pnl_pct("long", entry, mark)

    atr = _atr(df)
    stop_below = entry * 0.94 if atr is None else entry - 2.0 * atr
    v.key_levels["entry"] = entry
    v.key_levels["stop"] = stop_below

    # Hard stop: a reversal trade that loses money fast is wrong
    if mark < stop_below:
        v.add("exit", "high", f"Stop ${stop_below:.2f} hit — reversal failed, get out")

    # Failed reversal: new low after entry kills the thesis
    low_52w = df["Low"].tail(252).min() if len(df) >= 5 else None
    if low_52w is not None and float(latest["Low"]) <= float(low_52w):
        v.add("exit", "high", f"New 52w low (${float(low_52w):.2f}) post-entry — divergence broke")

    # Take profit on RSI normalization
    rsi = latest.get("RSI_14")
    if pd.notna(rsi) and rsi >= 60:
        v.add("trim", "medium", f"RSI {rsi:.0f} — momentum has rotated, lock in some gains")

    # Structural progress: reclaiming MA50 is real validation
    ma50 = latest.get("MA_50")
    if pd.notna(ma50) and mark > ma50 and pnl > 0:
        v.key_levels["raise_stop_to"] = entry
        v.add("trim", "low", f"Reclaimed 50d MA (${ma50:.2f}) — raise stop to entry, let it run")

    # Big runner
    if pnl >= 20:
        v.add("trim", "medium", f"Up {pnl:.1f}% on a reversal trade — trim aggressively, rare runner")


def _evaluate_caution(trade: dict, df: pd.DataFrame, v: ExitVerdict) -> None:
    """Short — Bearish Divergence (new 52w high, lower RSI). Topping pattern."""
    latest = df.iloc[-1]
    entry = float(trade["entry_price"])
    mark = float(latest["Close"])
    pnl = _pnl_pct("short", entry, mark)

    atr = _atr(df)
    stop_above = entry * 1.05 if atr is None else entry + 2.0 * atr
    v.key_levels["entry"] = entry
    v.key_levels["stop"] = stop_above

    if mark >= stop_above:
        v.add("exit", "high", f"Stop ${stop_above:.2f} hit — divergence failed")

    # New 52w high after entry = thesis dead
    high_52w = df["High"].tail(252).max() if len(df) >= 5 else None
    if high_52w is not None and float(latest["High"]) >= float(high_52w):
        v.add("exit", "high", f"New 52w high (${float(high_52w):.2f}) — top is in… later, not now")

    # Take profit
    if pnl >= 8:
        v.add("trim", "medium", f"Down {pnl:.1f}% — partial cover, ride the rest")
    if pnl >= 15:
        v.add("exit", "high", f"Down {pnl:.1f}% — take the win")

    # MA50 break = real downtrend forming, hold
    ma50 = latest.get("MA_50")
    if pd.notna(ma50) and mark < ma50:
        v.key_levels["lower_stop_to"] = entry
        v.add("hold", "medium", f"Below 50d MA (${ma50:.2f}) — trend has rolled, hold and trail")

    # RSI flipping back to weakness = momentum confirms
    rsi = latest.get("RSI_14")
    if pd.notna(rsi) and rsi <= 40 and pnl > 0:
        v.add("trim", "low", f"RSI {rsi:.0f} — momentum confirms, partial cover into weakness")


def _evaluate_manual(trade: dict, df: pd.DataFrame, v: ExitVerdict) -> None:
    """Generic fallback for trades without a setup tag."""
    latest = df.iloc[-1]
    side = trade["side"]
    entry = float(trade["entry_price"])
    mark = float(latest["Close"])
    pnl = _pnl_pct(side, entry, mark)
    rsi = latest.get("RSI_14")
    ma50 = latest.get("MA_50")
    ma200 = latest.get("MA_200")

    v.key_levels["entry"] = entry
    v.key_levels["stop"] = entry * 0.92 if side == "long" else entry * 1.08

    if pd.notna(rsi):
        if side == "long" and rsi >= 70:
            v.add("trim", "medium", f"RSI {rsi:.0f} — overbought, take some profits")
        if side == "short" and rsi <= 30:
            v.add("trim", "medium", f"RSI {rsi:.0f} — oversold, cover some")

    if pd.notna(ma50):
        if side == "long" and mark < ma50:
            v.add("exit", "medium", f"Below 50d MA (${ma50:.2f}) — momentum weakening")
        if side == "short" and mark > ma50:
            v.add("exit", "medium", f"Above 50d MA (${ma50:.2f}) — short thesis weakening")

    if pnl >= 15:
        v.add("trim", "medium", f"Up {pnl:.1f}% — scale out, trail stop")
    if pnl <= -8:
        v.add("exit", "high", f"Down {abs(pnl):.1f}% — review thesis, consider stop")

    if pd.notna(ma200):
        if side == "long" and mark < ma200:
            v.add("exit", "high", f"Below 200d MA — long-term trend has flipped against you")
        if side == "short" and mark > ma200:
            v.add("exit", "high", f"Above 200d MA — long-term trend against the short")


_PLAYBOOKS = {
    "fade": _evaluate_fade,
    "momentum": _evaluate_momentum,
    "reversal": _evaluate_reversal,
    "caution": _evaluate_caution,
    "manual": _evaluate_manual,
}


def evaluate_exit(trade: dict, df: pd.DataFrame) -> ExitVerdict:
    """Route an open trade to its setup playbook and return a verdict."""
    v = ExitVerdict()
    if df is None or df.empty or len(df) < 2:
        v.context = "Not enough price data to evaluate."
        return v

    df = enrich_ohlcv_with_indicators(df)
    setup = (trade.get("setup") or "manual").lower()
    playbook = _PLAYBOOKS.get(setup, _evaluate_manual)
    playbook(trade, df, v)

    # Build the one-liner context
    if v.action == "exit":
        v.context = f"Action: EXIT ({v.confidence} conviction)"
    elif v.action == "trim":
        v.context = f"Action: TRIM ({v.confidence} conviction)"
    else:
        v.context = "Action: HOLD — no exit rules fired"
    return v
