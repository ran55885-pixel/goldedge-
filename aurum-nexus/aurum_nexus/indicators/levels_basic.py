"""Reference levels: PDH/PDL, PWH/PWL, daily/weekly open, Asian range, round numbers, pivot S/R clusters.
No price level is hard-coded: everything is derived from data; round-number steps scale with ADR/price."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from aurum_nexus.indicators.structure import Swing
from aurum_nexus.models import Level
from aurum_nexus.timeutil import broker_day_ids, window_bounds


def daily_weekly(bars: pd.DataFrame, day_boundary: dict | None = None) -> dict:
    """From intraday bars (H1 or finer) compute today's open/high/low and previous day/week extremes."""
    out: dict = {}
    if bars is None or len(bars) < 2:
        return out
    t = bars["time"].to_numpy("int64")
    o = bars["open"].to_numpy(float)
    h = bars["high"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    c = bars["close"].to_numpy(float)
    days = broker_day_ids(t, day_boundary)
    weeks = (days + 3) // 7

    def groups(keys):
        # keys are non-decreasing in time order
        cut = np.flatnonzero(np.diff(keys)) + 1
        starts = np.concatenate([[0], cut])
        ends = np.concatenate([cut, [len(keys)]])
        return list(zip(starts, ends))

    gd = groups(days)
    gw = groups(weeks)
    s, e = gd[-1]
    out.update(daily_open=float(o[s]), today_high=float(h[s:e].max()), today_low=float(low[s:e].min()),
               day_start=int(t[s]))
    if len(gd) > 1:
        s, e = gd[-2]
        out.update(pdh=float(h[s:e].max()), pdl=float(low[s:e].min()), pdc=float(c[e - 1]))
    s, e = gw[-1]
    out.update(weekly_open=float(o[s]), week_start=int(t[s]))
    if len(gw) > 1:
        s, e = gw[-2]
        out.update(pwh=float(h[s:e].max()), pwl=float(low[s:e].min()))
    return out


def asian_range(bars: pd.DataFrame, now: float, window: dict) -> dict | None:
    """High/low of the most recent Asian range window (complete or in progress)."""
    if bars is None or len(bars) == 0:
        return None
    s, e = window_bounds(now, window)
    seg = bars[(bars["time"] >= s) & (bars["time"] < e)]
    if len(seg) == 0:
        return None
    return {"high": float(seg["high"].max()), "low": float(seg["low"].min()), "start": s, "end": e,
            "complete": now >= e}


def nice_step(x: float) -> float:
    """Round a positive step to a 'nice' number: 1, 2, 2.5, 5 x 10^k."""
    if x <= 0 or not math.isfinite(x):
        return 0.0
    k = math.floor(math.log10(x))
    base = x / 10 ** k
    for m in (1.0, 2.0, 2.5, 5.0, 10.0):
        if base <= m * 1.2:
            return m * 10 ** k
    return 10.0 * 10 ** k


def round_steps(price: float, adr_value: float | None, cfg: dict) -> list[float]:
    if adr_value and adr_value > 0:
        fr = cfg.get("adr_fractions", [0.25, 0.5, 1.0])
        steps = [nice_step(adr_value * f) for f in fr]
    else:
        fr = cfg["price_fractions_fallback"]   # configured in symbols.levels_round_steps
        steps = [nice_step(price * f) for f in fr]
    return sorted({s for s in steps if s > 0})


def round_levels(price: float, adr_value: float | None, cfg: dict, span_adr: float = 1.5) -> list[Level]:
    steps = round_steps(price, adr_value, cfg)
    if not steps:
        return []
    span = (adr_value or price * 0.01) * span_adr
    out: dict[float, Level] = {}
    for rank, st in enumerate(steps):
        lo = math.floor((price - span) / st) * st
        x = lo
        while x <= price + span:
            key = round(x, 10)
            strength = 0.3 + 0.2 * rank
            if key not in out or out[key].strength < strength:
                out[key] = Level(kind="round", low=x, high=x, timeframe="D1", side="both", strength=min(0.9, strength),
                                 meta={"step": st})
            x += st
    return list(out.values())


def pivot_clusters(swings: list[Swing], tol: float, timeframe: str, min_touches: int = 2) -> list[Level]:
    """Cluster swing pivots within tol into strong S/R levels (>= min_touches pivots)."""
    if not swings or tol <= 0:
        return []
    pts = sorted(swings, key=lambda s: s.price)
    clusters: list[list[Swing]] = [[pts[0]]]
    for s in pts[1:]:
        if s.price - clusters[-1][-1].price <= tol:
            clusters[-1].append(s)
        else:
            clusters.append([s])
    out = []
    for cl in clusters:
        if len(cl) < min_touches:
            continue
        prices = np.array([s.price for s in cl])
        kinds = {s.kind for s in cl}
        side = "both" if len(kinds) == 2 else ("resistance" if "H" in kinds else "support")
        out.append(Level(kind="sr", low=float(prices.min()), high=float(prices.max()), timeframe=timeframe, side=side,
                         strength=min(1.0, 0.3 + 0.15 * len(cl)), touches=len(cl),
                         created=max(s.time for s in cl), meta={"pivots": len(cl)}))
    return out
