"""Candle confirmations: engulfing, pin/rejection, strong close."""
from __future__ import annotations

import pandas as pd


def _row(bars: pd.DataFrame, i: int):
    r = bars.iloc[i]
    return float(r.open), float(r.high), float(r.low), float(r.close)


def engulfing(bars: pd.DataFrame, i: int = -1) -> str | None:
    if bars is None or len(bars) < 2:
        return None
    o1, h1, l1, c1 = _row(bars, i - 1 if i < 0 else i - 1)
    o2, h2, l2, c2 = _row(bars, i)
    if c1 < o1 and c2 > o2 and c2 >= o1 and o2 <= c1 and (c2 - o2) > (o1 - c1):
        return "bull"
    if c1 > o1 and c2 < o2 and c2 <= o1 and o2 >= c1 and (o2 - c2) > (c1 - o1):
        return "bear"
    return None


def rejection(bars: pd.DataFrame, i: int = -1, wick_ratio: float = 0.5) -> str | None:
    """Pin / rejection wick: dominant wick >= wick_ratio of range and >= 1.5x body."""
    if bars is None or len(bars) < 1:
        return None
    o, h, low, c = _row(bars, i)
    rng = h - low
    if rng <= 0:
        return None
    body = abs(c - o)
    lower = min(o, c) - low
    upper = h - max(o, c)
    if lower >= wick_ratio * rng and lower >= 1.5 * body:
        return "bull"
    if upper >= wick_ratio * rng and upper >= 1.5 * body:
        return "bear"
    return None


def wick_ratio(bars: pd.DataFrame, i: int, direction: str) -> float:
    o, h, low, c = _row(bars, i)
    rng = h - low
    if rng <= 0:
        return 0.0
    return ((min(o, c) - low) if direction == "long" else (h - max(o, c))) / rng


def close_location(bars: pd.DataFrame, i: int = -1) -> float:
    o, h, low, c = _row(bars, i)
    rng = h - low
    return 0.0 if rng <= 0 else ((c - low) - (h - c)) / rng
