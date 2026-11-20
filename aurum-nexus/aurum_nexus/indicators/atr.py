"""ATR (Wilder true-range measure), ADR, ADR-used % and ATR percentile regime."""
from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(h: np.ndarray, low: np.ndarray, c: np.ndarray) -> np.ndarray:
    prev_c = np.concatenate([[c[0]], c[:-1]]) if len(c) else c
    return np.maximum.reduce([h - low, np.abs(h - prev_c), np.abs(low - prev_c)]) if len(c) else np.array([])


def atr(h: np.ndarray, low: np.ndarray, c: np.ndarray, n: int = 14) -> np.ndarray:
    """Wilder ATR. First value = plain range mean of the first n true ranges."""
    tr = true_range(np.asarray(h, float), np.asarray(low, float), np.asarray(c, float))
    out = np.full(len(tr), np.nan)
    if len(tr) == 0:
        return out
    if len(tr) < n:
        out[:] = np.cumsum(tr) / np.arange(1, len(tr) + 1)
        return out
    out[: n - 1] = np.cumsum(tr[: n - 1]) / np.arange(1, n)
    out[n - 1] = tr[:n].mean()
    for i in range(n, len(tr)):
        out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


def atr_last(bars: pd.DataFrame, n: int = 14) -> float | None:
    if bars is None or len(bars) < 2:
        return None
    a = atr(bars["high"].to_numpy(), bars["low"].to_numpy(), bars["close"].to_numpy(), n)
    v = float(a[-1])
    return v if np.isfinite(v) and v > 0 else None


def adr(daily: pd.DataFrame, n: int = 14, include_last: bool = False) -> float | None:
    """Average daily range of the last n COMPLETED days (the last row is treated as today unless include_last)."""
    if daily is None or len(daily) < 2:
        return None
    d = daily if include_last else daily.iloc[:-1]
    rng = (d["high"] - d["low"]).to_numpy()[-n:]
    rng = rng[rng > 0]
    return float(rng.mean()) if len(rng) else None


def adr_used_pct(today_high: float, today_low: float, adr_value: float | None) -> float | None:
    if not adr_value:
        return None
    return 100.0 * (today_high - today_low) / adr_value


def atr_percentile(atr_series: np.ndarray, lookback: int = 100) -> float | None:
    a = np.asarray(atr_series, float)
    a = a[np.isfinite(a)]
    if len(a) < 20:
        return None
    window = a[-lookback:]
    return float((window < window[-1]).mean() * 100.0)


def atr_regime(pct: float | None) -> str:
    if pct is None:
        return "n/a"
    if pct < 20:
        return "compression"
    if pct > 80:
        return "expansion"
    return "normal"
