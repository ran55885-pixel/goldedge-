"""Market structure: confirmed fractal swings, HH/HL/LH/LL labels, BOS vs CHoCH, dealing range.

All computations use CLOSED bars only. A swing at index j is confirmed only after k bars on its right.
BOS  = close beyond the active swing in the direction of the current trend (continuation).
CHoCH = first close beyond the protective swing AGAINST the current trend (change of character).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass
class Swing:
    idx: int
    time: int
    price: float
    kind: str        # H | L
    label: str = ""  # HH | LH | HL | LL | H | L
    confirmed_idx: int = 0


@dataclass
class StructEvent:
    idx: int
    time: int
    price: float      # the broken swing price
    kind: str         # BOS | CHoCH
    direction: str    # bull | bear
    swing_idx: int


def find_swings(h: np.ndarray, low: np.ndarray, k: int = 3) -> list[Swing]:
    n = len(h)
    out: list[Swing] = []
    for j in range(k, n - k):
        hv = h[j]
        if hv >= h[j - k:j].max() and hv > h[j + 1:j + k + 1].max():
            out.append(Swing(j, 0, float(hv), "H", confirmed_idx=j + k))
        lv = low[j]
        if lv <= low[j - k:j].min() and lv < low[j + 1:j + k + 1].min():
            out.append(Swing(j, 0, float(lv), "L", confirmed_idx=j + k))
    out.sort(key=lambda s: (s.idx, s.kind))
    return out


def label_swings(swings: list[Swing]) -> None:
    last_h = last_l = None
    for s in swings:
        if s.kind == "H":
            s.label = "H" if last_h is None else ("HH" if s.price > last_h.price else "LH")
            last_h = s
        else:
            s.label = "L" if last_l is None else ("HL" if s.price > last_l.price else "LL")
            last_l = s


def analyze(bars: pd.DataFrame, k: int = 3) -> dict:
    """Full structure analysis. Returns swings, events, trend, dealing range and premium/discount."""
    if bars is None or len(bars) < 2 * k + 3:
        return {"swings": [], "events": [], "trend": "neutral", "dealing_range": None, "position": None,
                "zone": "n/a", "last_high": None, "last_low": None}
    h = bars["high"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    c = bars["close"].to_numpy(float)
    t = bars["time"].to_numpy("int64")
    swings = find_swings(h, low, k)
    for s in swings:
        s.time = int(t[s.idx])
    label_swings(swings)
    by_confirm: dict[int, list[Swing]] = {}
    for s in swings:
        by_confirm.setdefault(s.confirmed_idx, []).append(s)
    trend = "neutral"
    active_h: Swing | None = None
    active_l: Swing | None = None
    events: list[StructEvent] = []
    for i in range(len(c)):
        for s in by_confirm.get(i, []):
            if s.kind == "H":
                active_h = s
            else:
                active_l = s
        if active_h is not None and c[i] > active_h.price and i > active_h.idx:
            kind = "CHoCH" if trend == "bear" else "BOS"
            events.append(StructEvent(i, int(t[i]), active_h.price, kind, "bull", active_h.idx))
            trend = "bull"
            active_h = None
        elif active_l is not None and c[i] < active_l.price and i > active_l.idx:
            kind = "CHoCH" if trend == "bull" else "BOS"
            events.append(StructEvent(i, int(t[i]), active_l.price, kind, "bear", active_l.idx))
            trend = "bear"
            active_l = None
    highs = [s for s in swings if s.kind == "H"]
    lows = [s for s in swings if s.kind == "L"]
    dr = None
    position = None
    zone = "n/a"
    if highs and lows:
        # dealing range = last swing high / last swing low, extended by any price beyond them since
        hi = max(highs[-1].price, float(h[highs[-1].idx:].max()))
        lo = min(lows[-1].price, float(low[lows[-1].idx:].min()))
        if hi > lo:
            dr = {"high": hi, "low": lo, "eq": (hi + lo) / 2}
            position = float((c[-1] - lo) / (hi - lo))
            zone = "premium" if position > 0.5 else "discount"
    return {
        "swings": swings, "events": events, "trend": trend, "dealing_range": dr, "position": position,
        "zone": zone, "last_high": highs[-1] if highs else None, "last_low": lows[-1] if lows else None,
        "active_high": active_h, "active_low": active_l,
    }


def recent_event(st: dict, within_bars: int, n_bars: int, kind: str | None = None,
                 direction: str | None = None) -> StructEvent | None:
    for ev in reversed(st.get("events", [])):
        if n_bars - 1 - ev.idx > within_bars:
            return None
        if (kind is None or ev.kind == kind) and (direction is None or ev.direction == direction):
            return ev
    return None


def to_jsonable(st: dict, limit: int = 60) -> dict:
    return {
        "trend": st.get("trend"), "zone": st.get("zone"), "position": st.get("position"),
        "dealing_range": st.get("dealing_range"),
        "swings": [asdict(s) for s in st.get("swings", [])[-limit:]],
        "events": [asdict(e) for e in st.get("events", [])[-limit:]],
    }
