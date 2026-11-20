"""Supply / demand zones from CLOSED bars (base + impulse).

Demand: 1..base_max_bars small candles (range <= base_max_range_atr*ATR) followed by a bullish impulse
        candle (range >= impulse_atr_mult*ATR, body/range >= impulse_body_ratio).
        Zone = [lowest low of base, highest body top of base].
Supply: mirror image. Zone = [lowest body bottom of base, highest high of base].
Score inputs: freshness (touches), departure strength (impulse range / ATR), time at base (fewer bars
better), HTF confluence (added by the Levels agent). A zone expires after `max_touches` touches or a
close through its far edge.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aurum_nexus.indicators.atr import atr
from aurum_nexus.models import Level


def find_zones(bars: pd.DataFrame, timeframe: str, impulse_atr_mult: float = 1.5, impulse_body_ratio: float = 0.55,
               base_max_bars: int = 3, base_max_range_atr: float = 0.9, max_touches: int = 2,
               max_zones: int = 12, atr_n: int = 14) -> list[Level]:
    if bars is None or len(bars) < atr_n + 5:
        return []
    o = bars["open"].to_numpy(float)
    h = bars["high"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    c = bars["close"].to_numpy(float)
    t = bars["time"].to_numpy("int64")
    a = atr(h, low, c, atr_n)
    vol = bars["real_volume"].to_numpy(float)
    if vol.sum() <= 0:
        vol = bars["tick_volume"].to_numpy(float)
    n = len(c)
    zones: list[Level] = []
    for i in range(atr_n, n):
        ref_atr = a[i - 1]
        if not np.isfinite(ref_atr) or ref_atr <= 0:
            continue
        rng = h[i] - low[i]
        body = abs(c[i] - o[i])
        if rng < impulse_atr_mult * ref_atr or rng == 0 or body / rng < impulse_body_ratio:
            continue
        bull = c[i] > o[i]
        # collect base candles immediately before the impulse
        base = []
        j = i - 1
        while j >= 0 and len(base) < base_max_bars and (h[j] - low[j]) <= base_max_range_atr * a[j] \
                and np.isfinite(a[j]):
            base.append(j)
            j -= 1
        if not base:
            continue
        if bull:
            zlow = float(low[base].min())
            zhigh = float(max(max(o[b], c[b]) for b in base))
            kind, side = "demand", "support"
        else:
            zhigh = float(h[base].max())
            zlow = float(min(min(o[b], c[b]) for b in base))
            kind, side = "supply", "resistance"
        if zhigh <= zlow:
            zhigh = zlow + 0.1 * ref_atr
        departure = rng / ref_atr
        # zone must not have been immediately invalidated by the impulse itself
        touches = 0
        inside = False
        alive = True
        for k in range(i + 1, n):
            if bull:
                if c[k] < zlow:
                    alive = False
                    break
                hit = low[k] <= zhigh
            else:
                if c[k] > zhigh:
                    alive = False
                    break
                hit = h[k] >= zlow
            if hit and not inside:
                touches += 1
                inside = True
            elif not hit:
                inside = False
        if not alive or touches > max_touches:
            continue
        mean_vol = vol[max(0, i - 20):i].mean() if i > 0 else 0
        react = float(vol[i] / mean_vol) if mean_vol > 0 else 0.0
        time_score = 1.0 - 0.5 * (len(base) - 1) / max(1, base_max_bars - 1) if base_max_bars > 1 else 1.0
        dep_score = min(1.0, departure / 3.0)
        fresh_score = {0: 1.0, 1: 0.6, 2: 0.3}.get(touches, 0.1)
        strength = 0.4 * dep_score + 0.25 * time_score + 0.35 * fresh_score
        zones.append(Level(kind=kind, low=zlow, high=zhigh, timeframe=timeframe, side=side, strength=strength,
                           fresh=touches == 0, touches=touches, created=int(t[base[-1]]), reaction_volume=react,
                           meta={"impulse_idx": int(i), "base_bars": len(base), "departure_atr": round(departure, 2),
                                 "in_zone_now": bool(inside)}))
    # de-overlap: keep the stronger of overlapping same-kind zones
    zones.sort(key=lambda z: -z.strength)
    kept: list[Level] = []
    for z in zones:
        if any(k.kind == z.kind and not (z.high < k.low or z.low > k.high) for k in kept):
            continue
        kept.append(z)
    kept.sort(key=lambda z: -z.created)
    return kept[:max_zones]
