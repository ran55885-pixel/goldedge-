"""Anchored VWAP with volume-weighted standard-deviation bands (±1σ, ±2σ).

Daily VWAP anchors at the broker day start, weekly at the broker week start, anchored VWAP at any
time (e.g. a swing). With no volume available the same calculation is time-weighted (labelled TWAP)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from aurum_nexus.models import volume_series


def anchored(bars: pd.DataFrame, anchor_time: float) -> dict | None:
    if bars is None or len(bars) == 0:
        return None
    seg = bars[bars["time"] >= anchor_time]
    if len(seg) == 0:
        return None
    tp = ((seg["high"] + seg["low"] + seg["close"]) / 3.0).to_numpy(float)
    v = volume_series(seg).to_numpy(float)
    basis = "VWAP"
    if v.sum() <= 0:
        v = np.ones(len(seg))
        basis = "TWAP (no volume)"
    cv = np.cumsum(v)
    cpv = np.cumsum(tp * v)
    cp2v = np.cumsum(tp * tp * v)
    vw = cpv / cv
    var = np.maximum(cp2v / cv - vw * vw, 0.0)
    sd = np.sqrt(var)
    return {
        "time": seg["time"].to_numpy("int64"), "vwap": vw, "sd": sd,
        "u1": vw + sd, "l1": vw - sd, "u2": vw + 2 * sd, "l2": vw - 2 * sd,
        "last": float(vw[-1]), "last_sd": float(sd[-1]), "basis": basis, "anchor": float(anchor_time),
    }


def band_position(price: float, vw: dict | None) -> float | None:
    """Distance from VWAP in σ units (positive above)."""
    if not vw or vw["last_sd"] <= 0:
        return None
    return (price - vw["last"]) / vw["last_sd"]


def acceptance(bars: pd.DataFrame, vw: dict | None, n: int = 6) -> str:
    """'above' if the last n closes are all above VWAP, 'below' if all below, else 'mixed'."""
    if not vw or bars is None or len(bars) < n:
        return "n/a"
    t = bars["time"].to_numpy("int64")[-n:]
    c = bars["close"].to_numpy(float)[-n:]
    vt = vw["time"]
    idx = np.searchsorted(vt, t, side="right") - 1
    if (idx < 0).any():
        return "n/a"
    ref = vw["vwap"][idx]
    if (c > ref).all():
        return "above"
    if (c < ref).all():
        return "below"
    return "mixed"


def to_series(vw: dict | None, limit: int = 1500) -> dict:
    if not vw:
        return {}
    out = {}
    for k in ("vwap", "u1", "l1", "u2", "l2"):
        out[k] = [{"time": int(t), "value": float(v)} for t, v in zip(vw["time"][-limit:], vw[k][-limit:])]
    out["basis"] = vw["basis"]
    return out
