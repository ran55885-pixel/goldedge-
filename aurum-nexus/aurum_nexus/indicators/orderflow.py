"""Order-flow ESTIMATES from tick volume (no real exchange order flow exists for OTC FX/gold).

delta       : per-bar delta estimate = volume x close-location value  ((c-l)-(h-c))/(h-l)
              or intra-bar M1 up/down estimate (sum of sign(close-open) x volume of the M1 bars inside)
cvd         : cumulative delta reset at each session/day id
rvol        : bar volume / mean volume of the bars at the SAME time-of-day over the previous N days
divergence  : price higher high + CVD lower high (bearish) / price lower low + CVD higher low (bullish)
spike/climax, absorption (effort vs result), VSA no-demand / no-supply, stopping volume.
All outputs are labelled estimates in the UI.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aurum_nexus.indicators.atr import atr
from aurum_nexus.models import volume_series


def delta_clv(bars: pd.DataFrame) -> np.ndarray:
    h = bars["high"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    c = bars["close"].to_numpy(float)
    v = volume_series(bars).to_numpy(float)
    rng = h - low
    with np.errstate(divide="ignore", invalid="ignore"):
        clv = np.where(rng > 0, ((c - low) - (h - c)) / rng, 0.0)
    return clv * v


def delta_from_m1(bars: pd.DataFrame, m1: pd.DataFrame, tf_seconds: int) -> np.ndarray:
    """Intra-bar estimate: for each tf bar sum sign(close-open)*volume of its M1 bars."""
    if m1 is None or len(m1) == 0:
        return delta_clv(bars)
    v = volume_series(m1).to_numpy(float)
    sgn = np.sign(m1["close"].to_numpy(float) - m1["open"].to_numpy(float))
    key = (m1["time"].to_numpy("int64") // tf_seconds) * tf_seconds
    s = pd.Series(sgn * v).groupby(key).sum()
    out = np.array([s.get(int(t), np.nan) for t in bars["time"].to_numpy("int64")], dtype=float)
    fallback = delta_clv(bars)
    return np.where(np.isnan(out), fallback, out)


def cvd(delta: np.ndarray, reset_ids: np.ndarray | None = None) -> np.ndarray:
    if reset_ids is None:
        return np.cumsum(delta)
    out = np.empty_like(delta, dtype=float)
    acc = 0.0
    prev = None
    for i, (d, rid) in enumerate(zip(delta, reset_ids)):
        if rid != prev:
            acc = 0.0
            prev = rid
        acc += d
        out[i] = acc
    return out


def rvol(bars: pd.DataFrame, days: int = 10) -> np.ndarray:
    """Relative volume vs the same time-of-day slot over the previous `days` sessions (NaN if no history)."""
    t = bars["time"].to_numpy("int64")
    v = volume_series(bars).to_numpy(float)
    slot = t % 86400
    hist: dict[int, list[float]] = {}
    out = np.full(len(v), np.nan)
    for i in range(len(v)):
        lst = hist.setdefault(int(slot[i]), [])
        if lst:
            ref = sum(lst[-days:]) / len(lst[-days:])
            out[i] = v[i] / ref if ref > 0 else np.nan
        lst.append(v[i])
    # Sparse slot history (e.g. short data): fall back to ratio vs recent bars' typical volume
    if np.isnan(out).mean() > 0.5 and len(v) > 30:
        from numpy.lib.stride_tricks import sliding_window_view
        ref = np.median(sliding_window_view(v[:-1], 20), axis=1)  # ref for bar i = median of v[i-20:i]
        idx = np.arange(20, len(v))
        with np.errstate(divide="ignore", invalid="ignore"):
            fb = np.where(ref > 0, v[idx] / ref, np.nan)
        miss = np.isnan(out[idx])
        out[idx[miss]] = fb[miss]
    return out


def divergences(bars: pd.DataFrame, swings: list, cvd_arr: np.ndarray, max_lookback: int = 60) -> list[dict]:
    out = []
    n = len(bars)
    for kind in ("H", "L"):
        pts = [s for s in swings if s.kind == kind and s.idx < len(cvd_arr)]
        for a, b in zip(pts[:-1], pts[1:]):
            if b.idx - a.idx > max_lookback or n - b.idx > 300:
                continue
            if kind == "H" and b.price > a.price and cvd_arr[b.idx] < cvd_arr[a.idx]:
                out.append({"type": "bearish", "idx": b.idx, "time": b.time, "price": b.price, "prev_idx": a.idx,
                            "prev_time": a.time, "prev_price": a.price})
            if kind == "L" and b.price < a.price and cvd_arr[b.idx] > cvd_arr[a.idx]:
                out.append({"type": "bullish", "idx": b.idx, "time": b.time, "price": b.price, "prev_idx": a.idx,
                            "prev_time": a.time, "prev_price": a.price})
    return sorted(out, key=lambda d: d["idx"])


def flags(bars: pd.DataFrame, rv: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """Per-bar boolean flags for volume events."""
    o = bars["open"].to_numpy(float)
    h = bars["high"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    c = bars["close"].to_numpy(float)
    v = volume_series(bars).to_numpy(float)
    a = atr(h, low, c)
    rv = rvol(bars) if rv is None else rv
    rng = h - low
    n = len(c)
    rvf = np.nan_to_num(rv, nan=0.0)
    spike = rvf >= 2.0
    climax = (rvf >= 3.0) & (rng >= 1.5 * a)
    absorption = (rvf >= 1.5) & (rng <= 0.6 * a)
    no_demand = np.zeros(n, bool)
    no_supply = np.zeros(n, bool)
    stopping = np.zeros(n, bool)
    with np.errstate(divide="ignore", invalid="ignore"):
        clv = np.where(rng > 0, ((c - low) - (h - c)) / rng, 0.0)
    for i in range(5, n):
        narrow = rng[i] < 0.7 * a[i]
        lower_vol = v[i] < v[i - 1] and v[i] < v[i - 2]
        if c[i] > o[i] and narrow and lower_vol:
            no_demand[i] = True
        if c[i] < o[i] and narrow and lower_vol:
            no_supply[i] = True
        if c[i] < c[i - 5] and rvf[i] >= 2.0 and clv[i] > 0 and c[i] <= o[i] + 0.5 * rng[i]:
            stopping[i] = True
    return {"spike": spike, "climax": climax, "absorption": absorption, "no_demand": no_demand,
            "no_supply": no_supply, "stopping_volume": stopping, "rvol": rv}
