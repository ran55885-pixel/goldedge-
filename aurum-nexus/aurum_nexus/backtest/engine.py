"""Backtester - runs the IDENTICAL live pipeline (Engine: Scanner, Levels, Signal, Coordinator, Guardian,
Risk, Execution, Trade Manager, Analyst) over historical M1 data.

Cost model : spread from the CSV `spread` column (points) or backtest.default_spread_points; commission
             per lot (round turn) deducted from P/L; slippage (execution.slippage_points_backtest points)
             applied adversely to market entries and stop exits; limit/TP exits fill at their price.
Intra-bar  : each M1 bar is replayed as ticks O -> adverse extreme -> favourable extreme -> C, where
             'adverse' is relative to the open position (conservative: if SL and TP are both inside one bar
             the SL is assumed first). A bar opening beyond a stop (gap) fills at the open.
Clock      : the cycle runs 6 s after each M1 close (outside the 'first seconds of a candle' filter).
Not modelled: historical news (no archive -> the news filter is inactive unless manual blackouts are
             configured), historical COT/macro/sentiment, DOM, partial fills, broker stop-levels.
"""
from __future__ import annotations

import copy
import time
from typing import Callable

import numpy as np
import pandas as pd

from aurum_nexus.config import Config, config_from_dict, deep_merge
from aurum_nexus.data.base import MarketDataAdapter
from aurum_nexus.data.feed import PriceFeed
from aurum_nexus.data.history import HistoryStore
from aurum_nexus.data.specs import digits_for, generic_spec, make_converter
from aurum_nexus.engine import Engine
from aurum_nexus.models import Tick
from aurum_nexus.storage import Storage

STOP_EXITS = ("sl", "trail", "breakeven")


class BacktestAdapter(MarketDataAdapter):
    kind = "backtest"
    is_preview = False

    def __init__(self, store: HistoryStore, spread_points: dict, has_volume: dict[str, bool]):
        super().__init__()
        self.store = store
        self.now = 0.0
        self.spread_points = spread_points
        self._has_volume = has_volume
        self.override: dict[str, float] = {}
        self.connected = True
        self.label = "BACKTEST (historical M1)"

    def connect(self) -> bool:
        return True

    def resolve_symbol(self, canonical: str) -> str | None:
        return canonical if canonical in self.store.m1 else None

    def symbol_spec(self, canonical: str):
        conv = make_converter(lambda s: (self.tick(s).mid if self.tick(s) else None))
        return generic_spec(canonical, "USD", conv)

    def spread(self, symbol: str, row) -> float:
        pt = 10.0 ** -digits_for(symbol)
        sp = float(row["spread"]) if row is not None and float(row["spread"]) > 0 else \
            float(self.spread_points.get(symbol, self.spread_points.get("default", 10)))
        return sp * pt

    def tick(self, canonical: str) -> Tick | None:
        row = self.store.last_m1(canonical, self.now)
        if row is None:
            return None
        mid = self.override.get(canonical, float(row["close"]))
        s = self.spread(canonical, row)
        return Tick(canonical, self.now, mid - s / 2, mid + s / 2, source="BACKTEST")

    def bars(self, canonical: str, tf: str, count: int) -> pd.DataFrame:
        return self.store.bars(canonical, tf, self.now, count)

    def has_volume(self, canonical: str) -> bool:
        return self._has_volume.get(canonical, False)


def _cfg_for_backtest(cfg: Config, symbols: list[str], overrides: dict | None, balance: float) -> Config:
    data = deep_merge(cfg.data, {"symbols": {"enabled": symbols}, "system": {"price_source": "auto"}})
    data = deep_merge(data, {"intel": {"cot": {"enabled": False}, "macro": {"enabled": False},
                                       "sentiment": {"enabled": False}, "dom": {"enabled": False}},
                             "news": {"enabled": False}})
    if overrides:
        data = deep_merge(data, overrides)
    c = config_from_dict(data, [{"name": "BACKTEST", "kind": "paper_only", "starting_balance": balance,
                                 "currency": "USD", "risk_pct": float(cfg.get("risk.default_risk_pct", 0.5))}])
    c.config_dir = cfg.config_dir
    return c


def run_backtest(cfg: Config, data: dict[str, pd.DataFrame], symbols: list[str], start: float | None = None,
                 end: float | None = None, step: int = 1, balance: float = 10000.0, overrides: dict | None = None,
                 profile_overrides: dict | None = None, warmup_days: float = 3.0,
                 progress: Callable[[float, dict], None] | None = None) -> dict:
    """Run one backtest. Returns dict(trades, equity, stats, config...)."""
    t_start = time.time()
    bcfg = _cfg_for_backtest(cfg, symbols, overrides, balance)
    store = HistoryStore(data, bcfg.get("system.day_boundary"))
    has_vol = {s: bool((df["tick_volume"] > 0).mean() > 0.5 or (df["real_volume"] > 0).mean() > 0.5)
               for s, df in data.items()}
    adapter = BacktestAdapter(store, bcfg.get("backtest.default_spread_points"), has_vol)
    feed = PriceFeed(bcfg, simulated=adapter, clock=lambda: adapter.now)
    eng = Engine(bcfg, feed, storage=Storage(":memory:"), clock=lambda: adapter.now, background=False,
                 sleep=lambda s: None, persist_events=False)
    eng.strategist.save = lambda s: None          # never write profiles from a backtest
    if profile_overrides:
        from aurum_nexus.bots.strategist import _set, clamp_profile
        for sym, changes in profile_overrides.items():
            p = copy.deepcopy(eng.strategist.profile(sym))
            for path, v in changes.items():
                _set(p, path, v)
            eng.strategist.profiles[sym] = clamp_profile(p, bcfg.min_rr)[0]
    h = eng.accounts.handles["BACKTEST"]
    comm = float(bcfg.get("backtest.commission_per_lot", 0.0))
    h.paper.commission_per_lot = comm
    h.paper.slippage_points = float(bcfg.get("execution.slippage_points_backtest", 0))
    # timeline
    main = [s for s in symbols if s in data]
    if not main:
        raise ValueError("no data for the requested symbols")
    times = np.unique(np.concatenate([data[s]["time"].to_numpy("int64") for s in main]))
    t0 = float(times[0]) + warmup_days * 86400
    if start is not None:
        t0 = max(t0, float(start))
    t1 = float(end) if end is not None else float(times[-1]) + 60
    run_times = times[(times >= t0) & (times < t1)]
    if len(run_times) == 0:
        raise ValueError("no bars inside the backtest window (after warm-up)")
    equity: list[tuple[float, float]] = []
    last_eq_t = 0.0
    m1 = {s: data[s].set_index("time") for s in main}
    for i, bt in enumerate(run_times):
        close_t = float(bt) + 60
        # ---- intra-bar replay for open positions (bar bt is complete at close_t)
        adapter.now = close_t + 6
        if eng.book.open:
            _intrabar(eng, adapter, m1, int(bt), close_t + 6)
        adapter.override.clear()
        if i % max(1, step) == 0:
            eng.cycle()
        else:
            eng._manage(eng.scanner.contexts, eng.levels.dxy.state, adapter.now)
        if adapter.now - last_eq_t >= 3600:
            last_eq_t = adapter.now
            equity.append((adapter.now, h.paper.account().equity))
        if progress and i % 500 == 0:
            progress(i / len(run_times), {"time": adapter.now, "trades": len(eng.book.closed())})
    # close anything still open at the end (marked end_of_test)
    for t in list(eng.book.open.values()):
        eng.execution.close(t, h, 1.0, "end of backtest", "end_of_test", adapter.now)
    equity.append((adapter.now, h.paper.account().equity))
    trades = sorted(eng.book.closed(), key=lambda t: t["open_ts"])
    for t in trades:
        t["commission"] = comm * float(t["volume"])
        t["pnl_net"] = float(t.get("pnl") or 0.0) - t["commission"]
    planned = [min((t.get("meta") or {}).get("rr_planned", {}).values() or [1.0]) for t in trades]
    if any(p < cfg.min_rr - 1e-6 for p in planned):
        raise AssertionError("a trade was opened with planned RR below the 1:1 floor")
    return {
        "trades": trades, "equity": equity, "symbols": main, "start": t0, "end": float(run_times[-1]) + 60,
        "balance": balance, "final_equity": equity[-1][1], "runtime_s": round(time.time() - t_start, 1),
        "signals": eng.storage.query("SELECT id, ts, symbol, setup, timeframe, direction, score FROM signals"),
        "fired": dict(eng.tm.fired), "shadows": eng.storage.shadows("closed"), "step": step,
        "has_volume": has_vol, "min_planned_rr": min(planned) if planned else None,
        "commission_per_lot": comm, "slippage_points": h.paper.slippage_points,
    }


def _intrabar(eng: Engine, adapter: BacktestAdapter, m1: dict, bt: int, now: float) -> None:
    """Replay the just-closed M1 bar as a conservative tick path for each symbol with open trades."""
    by_sym: dict[str, list] = {}
    for t in eng.book.open.values():
        by_sym.setdefault(t["symbol"], []).append(t)
    h = eng.accounts.handles["BACKTEST"]
    for sym, trades in by_sym.items():
        df = m1.get(sym)
        if df is None or bt not in df.index:
            continue
        r = df.loc[bt]
        o, hi, lo, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
        long_bias = sum(1 if t["direction"] == "long" else -1 for t in trades) >= 0
        path = [o, lo, hi] if long_bias else [o, hi, lo]
        # gap: a bar opening beyond a stop fills at the open
        for t in list(trades):
            sign = 1 if t["direction"] == "long" else -1
            spread = adapter.spread(sym, r)
            exit_px = (o - spread / 2) if sign > 0 else (o + spread / 2)
            if (exit_px - t["stop"]) * sign < 0:
                eng.execution.close(t, h, 1.0, f"gap through stop at open {o:.6g}", "sl", now, price_hint=exit_px)
        for p in path:
            if not eng.book.open:
                break
            adapter.override[sym] = p
            eng._manage(eng.scanner.contexts, eng.levels.dxy.state, now)
