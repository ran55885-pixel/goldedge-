"""Bar utilities: normalisation, closed-bar filtering, resampling (M1 -> higher TFs)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from aurum_nexus.models import BAR_COLUMNS, TF_SECONDS, empty_bars
from aurum_nexus.timeutil import broker_day_ids, week_ids


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return empty_bars()
    out = df.copy()
    for c in BAR_COLUMNS:
        if c not in out:
            out[c] = 0.0
    out = out[BAR_COLUMNS]
    out["time"] = out["time"].astype("int64")
    for c in BAR_COLUMNS[1:]:
        out[c] = pd.to_numeric(out[c], errors="coerce").astype("float64")
    out = out.dropna(subset=["open", "high", "low", "close"])
    out = out.drop_duplicates("time", keep="last").sort_values("time").reset_index(drop=True)
    return out


def closed_only(bars: pd.DataFrame, tf: str, now: float) -> pd.DataFrame:
    """Drop the forming bar: keep bars whose close time (open + tf) <= now."""
    if bars is None or len(bars) == 0:
        return bars
    sec = TF_SECONDS[tf]
    if tf in ("D1", "W1"):
        # daily/weekly bars are closed when a later bar exists
        return bars.iloc[:-1].reset_index(drop=True) if len(bars) > 1 and bars["time"].iloc[-1] + sec > now else bars
    mask = (bars["time"].to_numpy() + sec) <= now
    return bars[mask].reset_index(drop=True)


def resample(m1: pd.DataFrame, tf: str, day_boundary: dict | None = None) -> pd.DataFrame:
    """Aggregate M1 (or any finer) bars into tf. D1 uses the broker day boundary; W1 groups broker weeks."""
    if m1 is None or len(m1) == 0:
        return empty_bars()
    if tf == "M1":
        return m1.reset_index(drop=True)
    t = m1["time"].to_numpy()
    if tf == "D1":
        keys = broker_day_ids(t, day_boundary)
    elif tf == "W1":
        keys = week_ids(t, day_boundary)
    else:
        sec = TF_SECONDS[tf]
        keys = (t // sec) * sec
    df = m1.assign(_k=keys)
    g = df.groupby("_k", sort=False)
    out = pd.DataFrame({
        "time": g["time"].first() if tf in ("D1", "W1") else g["_k"].first(),
        "open": g["open"].first(),
        "high": g["high"].max(),
        "low": g["low"].min(),
        "close": g["close"].last(),
        "tick_volume": g["tick_volume"].sum(),
        "real_volume": g["real_volume"].sum(),
        "spread": g["spread"].max(),
    }).reset_index(drop=True)
    out["time"] = out["time"].astype("int64")
    return out.sort_values("time").reset_index(drop=True)


def append_tick_to_bars(bars: pd.DataFrame, tf: str, ts: float, price: float) -> pd.DataFrame:
    """Update/append the forming bar with a live tick (no volume added: tick volume unknown)."""
    sec = TF_SECONDS.get(tf, 60)
    bt = int(ts // sec * sec)
    if len(bars) and int(bars["time"].iloc[-1]) == bt:
        i = bars.index[-1]
        bars.at[i, "high"] = max(bars.at[i, "high"], price)
        bars.at[i, "low"] = min(bars.at[i, "low"], price)
        bars.at[i, "close"] = price
        return bars
    if len(bars) and int(bars["time"].iloc[-1]) > bt:
        return bars
    row = {"time": bt, "open": price, "high": price, "low": price, "close": price, "tick_volume": 0.0,
           "real_volume": 0.0, "spread": 0.0}
    return pd.concat([bars, pd.DataFrame([row])], ignore_index=True)


def arrays(bars: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    return (bars["open"].to_numpy(float), bars["high"].to_numpy(float), bars["low"].to_numpy(float),
            bars["close"].to_numpy(float), bars["time"].to_numpy("int64"))
