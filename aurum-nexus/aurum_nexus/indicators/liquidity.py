"""Liquidity pools (equal highs / equal lows) and sweep detection."""
from __future__ import annotations

import numpy as np
import pandas as pd

from aurum_nexus.indicators.structure import Swing
from aurum_nexus.models import Level


def equal_levels(swings: list[Swing], bars: pd.DataFrame, tol: float, timeframe: str,
                 max_age_bars: int = 400) -> list[Level]:
    """Groups of >=2 swing highs (lows) within tol form buy-side (sell-side) liquidity pools.
    swept=True once price traded beyond the pool; reclaimed when it then closed back inside."""
    if not swings or bars is None or len(bars) == 0 or tol <= 0:
        return []
    h = bars["high"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    c = bars["close"].to_numpy(float)
    n = len(bars)
    out: list[Level] = []
    for kind in ("H", "L"):
        pts = [s for s in swings if s.kind == kind and n - s.idx <= max_age_bars]
        pts.sort(key=lambda s: s.price)
        groups: list[list[Swing]] = []
        for s in pts:
            if groups and abs(s.price - groups[-1][0].price) <= tol:
                groups[-1].append(s)
            else:
                groups.append([s])
        for g in groups:
            if len(g) < 2:
                continue
            last_idx = max(s.idx for s in g)
            if kind == "H":
                price = max(s.price for s in g)
                after_h = h[last_idx + 1:]
                swept_idx = np.nonzero(after_h > price)[0]
            else:
                price = min(s.price for s in g)
                after_l = low[last_idx + 1:]
                swept_idx = np.nonzero(after_l < price)[0]
            swept = len(swept_idx) > 0
            reclaimed = False
            if swept:
                k = last_idx + 1 + int(swept_idx[0])
                reclaimed = bool(c[k] < price) if kind == "H" else bool(c[k] > price)
            out.append(Level(kind="eqh" if kind == "H" else "eql", low=price, high=price, timeframe=timeframe,
                             side="resistance" if kind == "H" else "support",
                             strength=min(1.0, 0.4 + 0.15 * len(g)) * (0.5 if swept else 1.0), fresh=not swept,
                             touches=len(g), created=int(max(s.time for s in g)),
                             meta={"swept": swept, "reclaimed": reclaimed, "count": len(g),
                                   "liquidity": "buy-side" if kind == "H" else "sell-side"}))
    return out


def detect_sweep(bars: pd.DataFrame, level: float, side: str, lookback: int = 3) -> dict | None:
    """Sweep-and-reclaim of a level within the last `lookback` closed bars.
    side='high': wick above level, close back below -> bearish sweep.
    side='low' : wick below level, close back above -> bullish sweep."""
    if bars is None or len(bars) < 1:
        return None
    n = len(bars)
    k = min(lookback, n)
    hh = bars["high"].to_numpy(float)[n - k:]
    ll = bars["low"].to_numpy(float)[n - k:]
    cc = bars["close"].to_numpy(float)[n - k:]
    tt = bars["time"].to_numpy("int64")[n - k:]
    for i in range(k - 1, -1, -1):
        if side == "high" and hh[i] > level and cc[i] < level:
            return {"direction": "short", "time": int(tt[i]), "extreme": float(hh[i]), "level": level}
        if side == "low" and ll[i] < level and cc[i] > level:
            return {"direction": "long", "time": int(tt[i]), "extreme": float(ll[i]), "level": level}
    return None
