"""Per-symbol analysis context built by the Scanner and consumed by Levels / Signal / Trade Manager."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from aurum_nexus.models import Level, Tick


@dataclass
class TFData:
    tf: str
    bars: pd.DataFrame
    atr: float | None = None
    atr_series: np.ndarray | None = None
    structure: dict = field(default_factory=dict)
    zones: list[Level] = field(default_factory=list)
    fvgs: list[Level] = field(default_factory=list)
    obs: list[Level] = field(default_factory=list)
    liquidity: list[Level] = field(default_factory=list)
    pivots: list[Level] = field(default_factory=list)
    delta: np.ndarray | None = None
    cvd: np.ndarray | None = None
    rvol: np.ndarray | None = None
    flags: dict = field(default_factory=dict)
    divergences: list[dict] = field(default_factory=list)
    last_bar_time: int = 0

    @property
    def n(self) -> int:
        return 0 if self.bars is None else len(self.bars)


@dataclass
class SymbolContext:
    symbol: str
    now: float
    tick: Tick | None = None
    tf: dict[str, TFData] = field(default_factory=dict)
    daily: dict = field(default_factory=dict)       # daily_open, pdh, pdl, pwh, pwl, today_high/low, day_start ...
    asia: dict | None = None
    adr: float | None = None
    adr_used: float | None = None
    atr_pct: float | None = None
    atr_regime: str = "n/a"
    vp: dict[str, Any] = field(default_factory=dict)  # session, daily, prior_day, composite, weekly
    vwap: dict[str, Any] = field(default_factory=dict)  # daily, weekly, anchored
    regime: dict = field(default_factory=dict)
    session: str = "off"
    has_volume: bool = False
    source: str = ""
    source_label: str = ""
    preview: bool = True
    round_levels: list[Level] = field(default_factory=list)
    htf_bias: dict = field(default_factory=dict)
    spread: float | None = None
    spread_hist: list = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    updated: float = 0.0
    levels_top: list = field(default_factory=list)

    @property
    def price(self) -> float | None:
        if self.tick:
            return self.tick.mid
        m1 = self.tf.get("M1")
        if m1 is not None and m1.n:
            return float(m1.bars["close"].iloc[-1])
        return None

    def atr(self, tf: str) -> float | None:
        d = self.tf.get(tf)
        return d.atr if d else None

    def bars(self, tf: str) -> pd.DataFrame | None:
        d = self.tf.get(tf)
        return d.bars if d else None
