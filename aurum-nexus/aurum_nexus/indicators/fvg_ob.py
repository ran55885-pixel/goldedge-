"""Fair value gaps and order blocks, kept until filled / mitigated."""
from __future__ import annotations

import numpy as np
import pandas as pd

from aurum_nexus.indicators.atr import atr
from aurum_nexus.models import Level


def find_fvgs(bars: pd.DataFrame, timeframe: str, min_atr: float = 0.1, keep: int = 10) -> list[Level]:
    """3-candle imbalance. Bullish: high[i-2] < low[i]; bearish: low[i-2] > high[i].
    A gap is removed once fully filled; partially filled gaps are kept with mitigated=True."""
    if bars is None or len(bars) < 20:
        return []
    h = bars["high"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    c = bars["close"].to_numpy(float)
    t = bars["time"].to_numpy("int64")
    a = atr(h, low, c)
    n = len(c)
    out: list[Level] = []
    # suffix extremes: min low / max high strictly after index i
    suf_min = np.full(n + 1, np.inf)
    suf_max = np.full(n + 1, -np.inf)
    for k in range(n - 1, -1, -1):
        suf_min[k] = min(suf_min[k + 1], low[k])
        suf_max[k] = max(suf_max[k + 1], h[k])
    for i in range(2, n):
        ref = a[i - 1] if np.isfinite(a[i - 1]) else 0
        if low[i] > h[i - 2] and (low[i] - h[i - 2]) >= min_atr * ref:
            g_lo, g_hi, kind = h[i - 2], low[i], "fvg_bull"
            filled = suf_min[i + 1] <= g_lo
            mitigated = suf_min[i + 1] < g_hi
        elif h[i] < low[i - 2] and (low[i - 2] - h[i]) >= min_atr * ref:
            g_lo, g_hi, kind = h[i], low[i - 2], "fvg_bear"
            filled = suf_max[i + 1] >= g_hi
            mitigated = suf_max[i + 1] > g_lo
        else:
            continue
        if filled:
            continue
        out.append(Level(kind=kind, low=float(g_lo), high=float(g_hi), timeframe=timeframe,
                         side="support" if kind == "fvg_bull" else "resistance",
                         strength=0.55 if not mitigated else 0.4, fresh=not bool(mitigated), created=int(t[i - 1]),
                         meta={"mitigated": bool(mitigated), "idx": int(i - 1)}))
    return out[-keep:]


def find_order_blocks(bars: pd.DataFrame, events: list, timeframe: str, keep: int = 8) -> list[Level]:
    """Order block = last opposite-colour candle before the impulse leg that produced a BOS/CHoCH.
    Bullish OB invalid once a close below its low; mitigated when price trades back into it."""
    if bars is None or len(bars) < 10 or not events:
        return []
    o = bars["open"].to_numpy(float)
    h = bars["high"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    c = bars["close"].to_numpy(float)
    t = bars["time"].to_numpy("int64")
    n = len(c)
    out: list[Level] = []
    seen: set[int] = set()
    for ev in events:
        start = ev.swing_idx
        end = ev.idx
        idx = None
        for j in range(end, max(start - 5, 0) - 1, -1):
            if ev.direction == "bull" and c[j] < o[j]:
                idx = j
                break
            if ev.direction == "bear" and c[j] > o[j]:
                idx = j
                break
        if idx is None or idx in seen:
            continue
        seen.add(idx)
        lo, hi = float(low[idx]), float(h[idx])
        mitigated = False
        alive = True
        for k in range(end + 1, n):
            if ev.direction == "bull":
                if c[k] < lo:
                    alive = False
                    break
                if low[k] <= hi:
                    mitigated = True
            else:
                if c[k] > hi:
                    alive = False
                    break
                if h[k] >= lo:
                    mitigated = True
        if not alive:
            continue
        kind = "ob_bull" if ev.direction == "bull" else "ob_bear"
        out.append(Level(kind=kind, low=lo, high=hi, timeframe=timeframe,
                         side="support" if ev.direction == "bull" else "resistance",
                         strength=(0.65 if ev.kind == "BOS" else 0.6) * (0.7 if mitigated else 1.0),
                         fresh=not mitigated, created=int(t[idx]),
                         meta={"mitigated": mitigated, "event": ev.kind, "idx": int(idx)}))
    return out[-keep:]
