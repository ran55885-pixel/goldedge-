"""SIMULATED data generator - for unit tests, the synthetic CSV tool and the explicit --simulate flag only.
Everything produced here is labelled SIMULATED and must never be presented as market data.

Model: per-minute log returns with session-dependent volatility, regime switching (trend / range /
expansion), occasional impulse legs (which create supply/demand zones and FVGs), mean reversion in
range regimes, and tick volume proportional to activity. Weekends are skipped (FX hours).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aurum_nexus.timeutil import is_weekend

SIM_START = {"XAUUSD": 2400.0, "XAGUSD": 29.0, "EURUSD": 1.0850, "GBPUSD": 1.2700, "USDJPY": 150.0,
             "AUDUSD": 0.6600, "NZDUSD": 0.6000, "USDCAD": 1.3600, "USDCHF": 0.8800, "EURJPY": 162.0,
             "GBPJPY": 190.0, "AUDJPY": 99.0, "USDSEK": 10.50}
SIM_VOL = {"XAUUSD": 0.00030, "XAGUSD": 0.00045, "USDJPY": 0.00012}
DEFAULT_VOL = 0.00010


def _session_mult(hour: int) -> float:
    if 7 <= hour < 12:
        return 1.5
    if 12 <= hour < 17:
        return 1.8
    if 17 <= hour < 21:
        return 0.9
    return 0.55


def generate_m1(symbol: str, start_ts: int, minutes: int, seed: int = 7, start_price: float | None = None,
                vol: float | None = None) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    price = float(start_price if start_price is not None else SIM_START.get(symbol, 1.0))
    sigma0 = vol if vol is not None else SIM_VOL.get(symbol, DEFAULT_VOL)
    digits = 2 if symbol.startswith("XAU") else (3 if symbol.endswith("JPY") or symbol.startswith("XAG") else 5)
    rows = []
    t = int(start_ts // 60 * 60)
    regime = "range"
    regime_left = 0
    drift = 0.0
    anchor = price
    impulse_left = 0
    impulse_dir = 0
    vol_state = 1.0
    produced = 0
    while produced < minutes:
        if is_weekend(t):
            t += 60
            continue
        hour = (t // 3600) % 24
        if regime_left <= 0:
            regime = rng.choice(["trend", "range", "expansion"], p=[0.4, 0.45, 0.15])
            regime_left = int(rng.integers(120, 900))
            drift = float(rng.normal(0, sigma0 * 0.08)) if regime != "range" else 0.0
            anchor = price
        regime_left -= 1
        vol_state = max(0.4, min(3.0, vol_state * np.exp(rng.normal(0, 0.03)) + (1.0 - vol_state) * 0.01))
        s = sigma0 * _session_mult(hour) * vol_state * (1.6 if regime == "expansion" else 1.0)
        if impulse_left <= 0 and rng.random() < 0.004:
            impulse_left = int(rng.integers(3, 9))
            impulse_dir = 1 if rng.random() < 0.5 else -1
        mu = drift
        if regime == "range":
            mu += -0.02 * np.log(price / anchor)
        if impulse_left > 0:
            mu += impulse_dir * s * 1.8
            impulse_left -= 1
        r = rng.normal(mu, s)
        o = price
        c = price * np.exp(r)
        wick = abs(rng.normal(0, s * 0.6)) * price
        h = max(o, c) + wick * rng.random()
        low = min(o, c) - wick * rng.random()
        activity = abs(r) / s if s > 0 else 1.0
        tv = max(1, int(rng.poisson(40 * _session_mult(hour) * (0.6 + 0.8 * activity))))
        spread = 25 if symbol.startswith("XAU") else (2 + int(rng.integers(0, 4)) if hour in range(6, 21) else 6)
        rows.append((t, round(o, digits), round(h, digits), round(low, digits), round(c, digits), tv, 0, spread))
        price = c
        t += 60
        produced += 1
    return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "tick_volume", "real_volume", "spread"])


def write_csv(df: pd.DataFrame, path: str) -> None:
    """Write in the documented backtest CSV format (docs/BACKTEST_CSV.md)."""
    out = df.copy()
    out["time"] = pd.to_datetime(out["time"], unit="s", utc=True).dt.strftime("%Y-%m-%d %H:%M:%S")
    out[["time", "open", "high", "low", "close", "tick_volume", "real_volume", "spread"]].to_csv(path, index=False)
