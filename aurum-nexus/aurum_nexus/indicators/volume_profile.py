"""Volume Profile (tick-volume based estimate): POC, VAH/VAL (value area %), HVN/LVN, profile shape.

Each bar's volume is spread uniformly over the price bins its range covers. With no volume the
profile becomes a time-at-price (TPO-like) profile and is labelled as such.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aurum_nexus.models import volume_series


def profile(bars: pd.DataFrame, rows: int = 80, va_pct: float = 0.70, use_volume: bool = True) -> dict | None:
    if bars is None or len(bars) < 3:
        return None
    h = bars["high"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    lo, hi = float(low.min()), float(h.max())
    if hi <= lo:
        return None
    vol = volume_series(bars).to_numpy(float) if use_volume else np.ones(len(bars))
    basis = "volume"
    if vol.sum() <= 0:
        vol = np.ones(len(bars))
        basis = "time (no volume)"
    edges = np.linspace(lo, hi, rows + 1)
    width = edges[1] - edges[0]
    hist = np.zeros(rows)
    for bh, bl, v in zip(h, low, vol):
        i0 = int(min(rows - 1, max(0, (bl - lo) // width)))
        i1 = int(min(rows - 1, max(0, (bh - lo) // width)))
        cnt = i1 - i0 + 1
        hist[i0:i1 + 1] += v / cnt
    centers = (edges[:-1] + edges[1:]) / 2
    poc_i = int(hist.argmax())
    total = hist.sum()
    lo_i = hi_i = poc_i
    acc = hist[poc_i]
    while acc < va_pct * total and (lo_i > 0 or hi_i < rows - 1):
        up = hist[hi_i + 1] if hi_i < rows - 1 else -1
        dn = hist[lo_i - 1] if lo_i > 0 else -1
        if up >= dn:
            hi_i += 1
            acc += up
        else:
            lo_i -= 1
            acc += dn
    med = float(np.median(hist[hist > 0])) if (hist > 0).any() else 0.0
    hvn, lvn = [], []
    for i in range(1, rows - 1):
        if hist[i] >= hist[i - 1] and hist[i] >= hist[i + 1] and hist[i] >= 1.3 * med and i != poc_i:
            hvn.append(float(centers[i]))
        if hist[i] <= hist[i - 1] and hist[i] <= hist[i + 1] and hist[i] <= 0.5 * med:
            lvn.append(float(centers[i]))
    poc = float(centers[poc_i])
    rel = (poc - lo) / (hi - lo)
    if 0.35 <= rel <= 0.65:
        shape = "balanced"
    elif rel > 0.65:
        shape = "P (buying tail / trend up)"
    else:
        shape = "b (selling tail / trend down)"
    return {"poc": poc, "vah": float(edges[hi_i + 1]), "val": float(edges[lo_i]), "hvn": hvn[:8], "lvn": lvn[:8],
            "low": lo, "high": hi, "bins": centers.tolist(), "volumes": hist.tolist(), "shape": shape,
            "balanced": shape == "balanced", "basis": basis, "bars": int(len(bars))}


def segment(bars: pd.DataFrame, start: float, end: float | None = None) -> pd.DataFrame:
    if bars is None or len(bars) == 0:
        return bars
    m = bars["time"] >= start
    if end is not None:
        m &= bars["time"] < end
    return bars[m]
