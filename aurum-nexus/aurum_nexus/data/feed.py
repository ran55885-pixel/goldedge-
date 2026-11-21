"""PriceFeed - chooses the price source per symbol and tracks freshness.

Priority (price_source=auto): MT5 analysis terminal -> OANDA v20 -> keyless fallback (PREVIEW ONLY).
Analysis, levels, volume tools, signals, SL/TP and fills use MT5 broker prices whenever an MT5
account is connected. If data is stale/missing the status says so (STALE / DISCONNECTED / NO DATA)
with the last real value and its timestamp - never a made-up number.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

import pandas as pd

from aurum_nexus.data.bars import closed_only
from aurum_nexus.data.base import MarketDataAdapter
from aurum_nexus.data.specs import generic_spec, make_converter
from aurum_nexus.models import TF_SECONDS, SymbolSpec, Tick, empty_bars
from aurum_nexus.timeutil import fmt_utc, is_weekend

BAR_TTL = {"M1": 5, "M5": 15, "M15": 30, "M30": 60, "H1": 120, "H4": 300, "D1": 600, "W1": 1800}


class PriceFeed:
    def __init__(self, cfg, mt5_sources: Callable[[], list[MarketDataAdapter]] | None = None,
                 oanda: MarketDataAdapter | None = None, fallback: MarketDataAdapter | None = None,
                 simulated: MarketDataAdapter | None = None, clock: Callable[[], float] | None = None):
        self.cfg = cfg
        self.mt5_sources = mt5_sources or (lambda: [])
        self.oanda = oanda
        self.fallback = fallback
        self.simulated = simulated
        self.clock = clock or time.time
        self.stale_after = float(cfg.get("system.stale_after_seconds", 6))
        self.preference = cfg.get("system.price_source", "auto")
        self._bars: dict[tuple[str, str, str], tuple[float, pd.DataFrame]] = {}
        self._last_tick: dict[str, Tick] = {}
        self._lock = threading.RLock()
        self.analysis_account = cfg.get("system.analysis_account", "auto")

    # ------------------------------------------------------------------ source selection
    def _mt5(self) -> MarketDataAdapter | None:
        srcs = [s for s in self.mt5_sources() if s is not None and s.connected]
        if not srcs:
            return None
        if self.analysis_account not in ("auto", "", None):
            for s in srcs:
                if getattr(s, "account_name", getattr(getattr(s, "acc", None), "name", None)) == self.analysis_account:
                    return s
        return srcs[0]

    def candidates(self) -> list[MarketDataAdapter]:
        if self.simulated is not None:
            return [self.simulated]
        pref = self.preference
        out: list[MarketDataAdapter] = []
        mt5 = self._mt5()
        oanda = self.oanda if (self.oanda is not None and self.oanda.connected) else None
        if pref == "mt5":
            out = [mt5]
        elif pref == "oanda":
            out = [oanda]
        elif pref == "fallback":
            out = [self.fallback]
        else:
            out = [mt5, oanda, self.fallback]
        return [s for s in out if s is not None]

    def source_for(self, symbol: str) -> MarketDataAdapter | None:
        for s in self.candidates():
            try:
                if s.resolve_symbol(symbol):
                    return s
            except Exception:
                continue
        return None

    def is_preview(self, symbol: str) -> bool:
        s = self.source_for(symbol)
        return True if s is None else bool(s.is_preview)

    # ------------------------------------------------------------------ data access
    def now(self) -> float:
        return self.clock()

    def tick(self, symbol: str) -> Tick | None:
        src = self.source_for(symbol)
        if src is None:
            return self._last_tick.get(symbol)
        try:
            t = src.tick(symbol)
        except Exception:
            t = None
        if t is not None:
            with self._lock:
                self._last_tick[symbol] = t
            return t
        return self._last_tick.get(symbol)

    def bars(self, symbol: str, tf: str, count: int = 500, closed: bool = True) -> pd.DataFrame:
        src = self.source_for(symbol)
        if src is None:
            return empty_bars()
        key = (src.kind, symbol, tf)
        now = self.clock()
        with self._lock:
            hit = self._bars.get(key)
        fresh = hit is not None and (now - hit[0]) < BAR_TTL.get(tf, 30) and len(hit[1]) >= min(count, 50)
        if hit is not None and fresh:
            # also refresh when a new bar should have closed since the fetch
            sec = TF_SECONDS[tf]
            if int(hit[0] // sec) != int(now // sec):
                fresh = False
        if not fresh:
            try:
                df = src.bars(symbol, tf, max(count, 50))
            except Exception:
                df = hit[1] if hit else empty_bars()
            with self._lock:
                self._bars[key] = (now, df)
        else:
            df = hit[1]
        out = closed_only(df, tf, now) if closed else df
        return out.tail(count).reset_index(drop=True)

    def spec(self, symbol: str, account_ccy: str = "USD") -> SymbolSpec | None:
        src = self.source_for(symbol)
        if src is not None and src.kind == "mt5":
            return src.symbol_spec(symbol)
        conv = make_converter(lambda s: (self.tick(s).mid if self.tick(s) else None))
        return generic_spec(symbol, account_ccy, conv)

    def has_volume(self, symbol: str) -> bool:
        src = self.source_for(symbol)
        return bool(src and src.has_volume(symbol))

    # ------------------------------------------------------------------ status
    def quote_status(self, symbol: str) -> dict:
        src = self.source_for(symbol)
        now = self.clock()
        t = self._last_tick.get(symbol)
        if src is None:
            return {"symbol": symbol, "state": "DISCONNECTED", "source": "none", "label": "NO SOURCE",
                    "preview": True, "last_tick": t.time if t else None,
                    "last_value": t.mid if t else None, "text": "DISCONNECTED"}
        if t is None:
            state = "NO DATA"
        else:
            age = now - t.time
            state = "LIVE" if age <= self.stale_after else "STALE"
            if state == "STALE" and is_weekend(now):
                state = "MARKET CLOSED"
        label = src.label
        if src.kind == "fallback" and hasattr(src, "preview_label"):
            label = src.preview_label(symbol)
        out = {"symbol": symbol, "state": state, "source": src.kind, "label": label, "preview": src.is_preview,
               "last_tick": t.time if t else None, "age": (now - t.time) if t else None,
               "bid": t.bid if t else None, "ask": t.ask if t else None, "last_value": t.mid if t else None,
               "has_volume": self.has_volume(symbol)}
        if t:
            out["text"] = f"last tick {fmt_utc(t.time)} · {max(0.0, now - t.time):.1f}s ago"
        else:
            out["text"] = "NO DATA"
        diff = self.oanda_mt5_diff(symbol)
        if diff is not None:
            out["oanda_mt5_diff"] = diff
        return out

    def oanda_mt5_diff(self, symbol: str) -> float | None:
        mt5 = self._mt5()
        if mt5 is None or self.oanda is None or not self.oanda.connected:
            return None
        try:
            a, b = mt5.tick(symbol), self.oanda.tick(symbol)
        except Exception:
            return None
        if not a or not b:
            return None
        return round(b.mid - a.mid, 6)

    def sources_status(self) -> list[dict]:
        out = []
        for s in [self._mt5(), self.oanda, self.fallback, self.simulated]:
            if s is not None:
                out.append(s.status())
        return out
