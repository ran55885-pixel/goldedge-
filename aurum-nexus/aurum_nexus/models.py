"""Core data structures shared by adapters, bots, engine and backtester.

Bars are pandas DataFrames with columns:
    time (int, epoch seconds UTC, bar OPEN time), open, high, low, close,
    tick_volume, real_volume, spread
"""
from __future__ import annotations

import time as _time
from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

BAR_COLUMNS = ["time", "open", "high", "low", "close", "tick_volume", "real_volume", "spread"]

TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400, "W1": 604800}
TIMEFRAMES = list(TF_SECONDS)


def empty_bars() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="float64") for c in BAR_COLUMNS}).astype({"time": "int64"})


def volume_series(bars: pd.DataFrame):
    """Real volume when non-zero, else tick volume (MT5 convention)."""
    if bars is None or len(bars) == 0:
        return pd.Series(dtype="float64")
    rv = bars["real_volume"] if "real_volume" in bars else None
    tv = bars["tick_volume"]
    if rv is not None and float(rv.abs().sum()) > 0:
        return rv.astype("float64")
    return tv.astype("float64")


def has_volume(bars: pd.DataFrame) -> bool:
    if bars is None or len(bars) == 0:
        return False
    v = volume_series(bars)
    return float((v > 0).mean()) > 0.5


@dataclass
class Tick:
    symbol: str
    time: float          # epoch seconds UTC
    bid: float
    ask: float
    last: float = 0.0
    volume: float = 0.0
    source: str = ""

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    def age(self, now: float | None = None) -> float:
        return (now if now is not None else _time.time()) - self.time


@dataclass
class SymbolSpec:
    name: str                    # canonical (XAUUSD)
    broker_name: str             # e.g. XAUUSD.m
    digits: int
    point: float
    tick_size: float
    tick_value: float            # value of one tick move for 1.0 lot in ACCOUNT currency (loss side)
    contract_size: float
    volume_min: float
    volume_max: float
    volume_step: float
    filling_mode: int = 3        # bit flags: 1=FOK, 2=IOC
    stops_level: int = 0
    currency_base: str = ""
    currency_profit: str = ""
    currency_margin: str = ""
    spread_points: float = 0.0
    tick_value_profit: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AccountSnapshot:
    name: str
    login: int | None = None
    server: str = ""
    company: str = ""
    holder: str = ""
    currency: str = ""
    balance: float | None = None
    equity: float | None = None
    profit: float | None = None
    margin: float | None = None
    margin_free: float | None = None
    margin_level: float | None = None
    leverage: int | None = None
    trade_mode: str = ""          # DEMO | CONTEST | REAL | PAPER
    time: float = 0.0             # snapshot time (epoch UTC)
    status: str = "NO DATA"       # OK | STALE | DISCONNECTED | NO DATA | PAUSED
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PositionInfo:
    ticket: int
    symbol: str
    side: str                     # buy | sell
    volume: float
    price_open: float
    price_current: float
    sl: float
    tp: float
    profit: float
    swap: float = 0.0
    magic: int = 0
    comment: str = ""
    time: float = 0.0             # open time epoch UTC
    tag: str = "OTHER"            # BOT | MANUAL | OTHER | PAPER
    account: str = ""
    r_multiple: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DealInfo:
    ticket: int
    order: int
    time: float                   # epoch UTC
    symbol: str
    type: str                     # buy | sell | balance | other
    entry: str                    # in | out | inout | out_by
    volume: float
    price: float
    profit: float
    commission: float = 0.0
    swap: float = 0.0
    fee: float = 0.0
    magic: int = 0
    position_id: int = 0
    comment: str = ""

    @property
    def net(self) -> float:
        return self.profit + self.commission + self.swap + self.fee


@dataclass
class OrderResult:
    ok: bool
    retcode: int
    price: float = 0.0
    volume: float = 0.0
    order: int = 0
    deal: int = 0
    comment: str = ""
    bid: float = 0.0
    ask: float = 0.0
    attempts: int = 1
    filling: str = ""
    slippage: float = 0.0


@dataclass
class Level:
    """A single price level / zone candidate from the Scanner."""
    kind: str                     # e.g. demand, supply, pdh, pdl, poc, vah, val, fvg_bull, ob_bear, eqh, round ...
    low: float
    high: float
    timeframe: str
    side: str = "both"            # support | resistance | both
    strength: float = 0.5         # 0..1 intrinsic quality
    fresh: bool = True
    touches: int = 0
    created: int = 0              # epoch seconds
    reaction_volume: float = 0.0  # relative (RVOL-like) reaction at formation
    meta: dict = field(default_factory=dict)

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["mid"] = self.mid
        return d


@dataclass
class Candidate:
    """A trade candidate produced by the Signal bot and processed by Coordinator/Guardian/Risk."""
    symbol: str
    direction: str                # long | short
    setup: str
    timeframe: str
    entry: float                  # planned entry (for limits) or reference price (market)
    entry_type: str               # market | limit
    stop: float
    tp1: float = 0.0
    tp2: float = 0.0
    obstacle: float | None = None # next opposing obstacle price
    score: float = 0.0
    base_score: float = 0.0
    components: dict = field(default_factory=dict)
    modifiers: dict = field(default_factory=dict)
    confirmations: list = field(default_factory=list)
    reasons: list = field(default_factory=list)
    zone_id: str = ""
    session: str = ""
    regime: str = ""
    created: float = 0.0
    status: str = "NEW"           # NEW | SELECTED | TAKEN | BLOCKED | SKIPPED | PENDING | IN TRADE
    status_reason: str = ""
    rr_tp1: float = 0.0
    rr_final: float = 0.0
    rr_obstacle: float | None = None
    size_factor: float = 1.0
    id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def now_ts() -> float:
    return _time.time()


def df_records(bars: pd.DataFrame, limit: int | None = None) -> list[dict[str, Any]]:
    if bars is None or len(bars) == 0:
        return []
    b = bars.tail(limit) if limit else bars
    return [
        {"time": int(r.time), "open": float(r.open), "high": float(r.high), "low": float(r.low),
         "close": float(r.close), "volume": float(r.tick_volume if not r.real_volume else r.real_volume)}
        for r in b.itertuples(index=False)
    ]
