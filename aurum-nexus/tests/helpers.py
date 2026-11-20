"""Test helpers: tiny fixed-price world (SIMULATED) for bot-level unit tests."""
from __future__ import annotations

import numpy as np

from aurum_nexus.bots.accounts import AccountHandle
from aurum_nexus.config import config_from_dict
from aurum_nexus.context import SymbolContext, TFData
from aurum_nexus.data.specs import generic_spec
from aurum_nexus.logbus import LogBus
from aurum_nexus.models import Candidate, Tick
from aurum_nexus.paper import PaperBroker
from aurum_nexus.storage import Storage
from aurum_nexus.tradebook import TradeBook

WED_10UTC = 1_700_042_400  # 2023-11-15 10:00 UTC (Wednesday, London session)


class World:
    def __init__(self, prices: dict[str, tuple[float, float]] | None = None, now: float = WED_10UTC):
        self.prices = prices or {"XAUUSD": (2400.00, 2400.30), "EURUSD": (1.08500, 1.08508),
                                 "GBPUSD": (1.27000, 1.27010), "USDJPY": (150.000, 150.012)}
        self.now = now

    def tick(self, s):
        if s not in self.prices:
            return None
        b, a = self.prices[s]
        return Tick(s, self.now, b, a, source="TEST")

    def spec(self, s):
        def conv(f, t):
            if f == t:
                return 1.0
            if f == "JPY" and t == "USD":
                return 1 / self.prices["USDJPY"][0]
            return None
        return generic_spec(s, "USD", conv)

    def clock(self):
        return self.now


def make_cfg(data=None, accounts=None):
    return config_from_dict(data or {}, accounts or [{"name": "P1", "kind": "paper_only", "starting_balance": 10000}])


def make_handle(cfg, world: World, name="P1", balance=10000.0):
    acc = cfg.account(name)
    pb = PaperBroker(name, balance, "USD", world.tick, world.spec, clock=world.clock)
    return AccountHandle(acc, None, pb)


def make_ctx(symbol, world: World, atr_m5=None, preview=False):
    t = world.tick(symbol)
    ctx = SymbolContext(symbol=symbol, now=world.now, tick=t)
    a = atr_m5 if atr_m5 is not None else t.mid * 0.0006
    import pandas as pd
    bars = pd.DataFrame({"time": [int(world.now) - 300 * i for i in range(30, 0, -1)], "open": t.mid, "high": t.mid + a / 2,
                         "low": t.mid - a / 2, "close": t.mid, "tick_volume": 100.0, "real_volume": 0.0, "spread": 0.0})
    for tf in ("M1", "M5", "M15", "H1"):
        ctx.tf[tf] = TFData(tf=tf, bars=bars, atr=a * {"M1": 0.5, "M5": 1, "M15": 1.8, "H1": 3}[tf],
                            atr_series=np.full(30, a), structure={"events": [], "swings": []})
    ctx.preview = preview
    ctx.spread = t.spread
    ctx.session = "london"
    return ctx


def make_candidate(symbol="XAUUSD", direction="long", entry=2400.30, stop=2396.30, tp1=2404.30, tp2=2410.0,
                   obstacle=None, score=85.0, setup="sweep_reversal", tf="M5"):
    c = Candidate(symbol=symbol, direction=direction, setup=setup, timeframe=tf, entry=entry, entry_type="market",
                  stop=stop, tp1=tp1, tp2=tp2, obstacle=obstacle, score=score, base_score=score, created=WED_10UTC)
    c.id = f"{symbol}-{setup}-{direction}-{score}"
    return c


def bus_and_book():
    st = Storage(":memory:")
    bus = LogBus(st)
    book = TradeBook(st)
    return st, bus, book
