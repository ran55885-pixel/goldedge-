"""HistoryStore - fast time-sliced access to historical bars (backtester and simulated mode).

All timeframes are resampled once from M1; `bars(symbol, tf, now)` returns the bars CLOSED at `now`
plus the forming bar built from the completed M1 bars since (exactly what a live terminal would show).
No look-ahead: an M1 bar is visible only once its close time (open + 60s) <= now.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aurum_nexus.data.bars import resample
from aurum_nexus.models import TF_SECONDS, empty_bars
from aurum_nexus.timeutil import _boundary_cached, broker_day_ids


class HistoryStore:
    def __init__(self, m1: dict[str, pd.DataFrame], day_boundary: dict | None = None):
        self.day_boundary = day_boundary or {"tz": "America/New_York", "time": "17:00"}
        self.m1 = {s: df.sort_values("time").reset_index(drop=True) for s, df in m1.items()}
        self.m1_time = {s: df["time"].to_numpy("int64") for s, df in self.m1.items()}
        self._full: dict[tuple[str, str], tuple[pd.DataFrame, np.ndarray]] = {}

    def symbols(self) -> list[str]:
        return list(self.m1)

    def _frame(self, symbol: str, tf: str) -> tuple[pd.DataFrame, np.ndarray]:
        key = (symbol, tf)
        if key not in self._full:
            df = resample(self.m1[symbol], tf, self.day_boundary)
            t = df["time"].to_numpy("int64")
            if tf == "D1":
                ids = broker_day_ids(t, self.day_boundary)
                end = np.array([_boundary_cached(int(d), self.day_boundary["tz"], self.day_boundary["time"])
                                for d in ids], dtype=float)
                # day id = date of the day END; the day ends at the boundary on that date
            elif tf == "W1":
                end = np.concatenate([t[1:], [np.inf]]).astype(float)
            else:
                end = (t + TF_SECONDS[tf]).astype(float)
            self._full[key] = (df, end)
        return self._full[key]

    def bars(self, symbol: str, tf: str, now: float, count: int = 500, include_forming: bool = True) -> pd.DataFrame:
        if symbol not in self.m1:
            return empty_bars()
        mt = self.m1_time[symbol]
        k = int(np.searchsorted(mt + 60, now, side="right"))  # completed M1 bars
        if k == 0:
            return empty_bars()
        if tf == "M1":
            return self.m1[symbol].iloc[max(0, k - count):k].reset_index(drop=True)
        df, end = self._frame(symbol, tf)
        nclosed = int(np.searchsorted(end, now, side="right"))
        closed = df.iloc[max(0, nclosed - count):nclosed]
        if not include_forming:
            return closed.reset_index(drop=True)
        start_forming = end[nclosed - 1] if nclosed > 0 else -np.inf
        if tf == "W1" and nclosed > 0:
            start_forming = df["time"].iloc[nclosed] if nclosed < len(df) else np.inf
        j = int(np.searchsorted(mt, start_forming, side="left"))
        if j >= k:
            return closed.reset_index(drop=True)
        seg = self.m1[symbol].iloc[j:k]
        if tf in ("D1", "W1"):
            ft = int(seg["time"].iloc[0])
        else:
            ft = int(seg["time"].iloc[0] // TF_SECONDS[tf] * TF_SECONDS[tf])
        row = {"time": ft, "open": float(seg["open"].iloc[0]), "high": float(seg["high"].max()),
               "low": float(seg["low"].min()), "close": float(seg["close"].iloc[-1]),
               "tick_volume": float(seg["tick_volume"].sum()), "real_volume": float(seg["real_volume"].sum()),
               "spread": float(seg["spread"].max())}
        out = pd.concat([closed, pd.DataFrame([row])], ignore_index=True)
        return out.tail(count).reset_index(drop=True)

    def last_m1(self, symbol: str, now: float):
        mt = self.m1_time[symbol]
        k = int(np.searchsorted(mt + 60, now, side="right"))
        return self.m1[symbol].iloc[k - 1] if k > 0 else None
