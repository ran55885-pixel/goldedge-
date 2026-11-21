"""MockAdapter - deterministic SIMULATED market + trading back-end for unit tests and --simulate only.

Every tick/bar it produces is labelled "SIMULATED". Order responses can be scripted to exercise
requotes, timeouts, filling-mode errors and slippage.
"""
from __future__ import annotations

import itertools
import time

import pandas as pd

from aurum_nexus.backtest.synthetic import generate_m1
from aurum_nexus.data.history import HistoryStore
from aurum_nexus.data.base import RET_DONE, RET_INVALID_FILL, RET_LOCAL_REJECT, TradingAdapter
from aurum_nexus.data.specs import generic_spec
from aurum_nexus.models import AccountSnapshot, DealInfo, OrderResult, PositionInfo, Tick, empty_bars


class MockAdapter(TradingAdapter):
    kind = "mock"
    is_preview = False

    def __init__(self, symbols: list[str] | None = None, start_ts: int | None = None, minutes: int = 6 * 1440,
                 seed: int = 11, clock_ts: float | None = None, data: dict[str, pd.DataFrame] | None = None,
                 balance: float = 10000.0, currency: str = "USD", name: str = "MOCK", magic: int = 770077):
        super().__init__()
        self.label = "SIMULATED"
        self.symbols = symbols or ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY"]
        self.m1: dict[str, pd.DataFrame] = {}
        start = start_ts or int(time.time()) - minutes * 60 * 2
        if data:
            self.m1 = {k: v.reset_index(drop=True) for k, v in data.items()}
        else:
            for i, s in enumerate(self.symbols):
                self.m1[s] = generate_m1(s, start, minutes, seed=seed + i)
        self.store = HistoryStore(self.m1)
        last = max(int(df["time"].iloc[-1]) for df in self.m1.values())
        self.now = clock_ts if clock_ts is not None else float(last + 60)
        self.spread_mult = 1.0
        self.name = name
        self.magic = magic
        self.balance = balance
        self.currency = currency
        self.trade_mode = "DEMO"
        self._pos: dict[int, PositionInfo] = {}
        self._deals: list[DealInfo] = []
        self._tickets = itertools.count(1000)
        self.script: list[tuple[int, float]] = []   # queued (retcode, slippage) responses for order_send
        self.sent: list[dict] = []
        self.allowed_fillings = {0, 1, 2}
        self.fail_connect = 0

    # ------------------------------------------------------------------ data
    def connect(self) -> bool:
        if self.fail_connect > 0:
            self.fail_connect -= 1
            self.connected = False
            self.last_error = "simulated connect failure"
            return False
        self.connected = True
        return True

    def resolve_symbol(self, canonical: str) -> str | None:
        return canonical if canonical in self.m1 else None

    def symbol_spec(self, canonical: str):
        if canonical not in self.m1:
            return None
        return generic_spec(canonical, self.currency, lambda f, t: self._convert(f, t))

    def _convert(self, frm: str, to: str):
        if frm == to:
            return 1.0
        for s, inv in ((frm + to, False), (to + frm, True)):
            if s in self.m1:
                p = self._last_close(s)
                return 1 / p if inv else p
        return None

    def _last_close(self, symbol: str) -> float:
        r = self.store.last_m1(symbol, self.now)
        return float(r["close"]) if r is not None else float(self.m1[symbol]["open"].iloc[0])

    def tick(self, canonical: str) -> Tick | None:
        if canonical not in self.m1:
            return None
        last = self.store.last_m1(canonical, self.now)
        if last is None:
            return None
        spec = self.symbol_spec(canonical)
        spr = float(last["spread"]) * spec.point * self.spread_mult
        mid = float(last["close"])
        t = Tick(canonical, self.now, mid - spr / 2, mid + spr / 2, source="SIMULATED")
        self.last_tick_time[canonical] = t.time
        return t

    def bars(self, canonical: str, tf: str, count: int) -> pd.DataFrame:
        if canonical not in self.m1:
            return empty_bars()
        return self.store.bars(canonical, tf, self.now, count)

    def advance(self, minutes: int = 1) -> None:
        self.now += 60 * minutes
        self._mark()

    # ------------------------------------------------------------------ account
    def _mark(self) -> None:
        for p in self._pos.values():
            t = self.tick(p.symbol)
            if not t:
                continue
            px = t.bid if p.side == "buy" else t.ask
            spec = self.symbol_spec(p.symbol)
            diff = (px - p.price_open) * (1 if p.side == "buy" else -1)
            p.price_current = px
            p.profit = diff / spec.tick_size * spec.tick_value * p.volume

    def account(self) -> AccountSnapshot:
        self._mark()
        fl = sum(p.profit for p in self._pos.values())
        return AccountSnapshot(name=self.name, login=1, server="SIM", company="SIMULATED", holder="SIM",
                               currency=self.currency, balance=self.balance, equity=self.balance + fl, profit=fl,
                               margin=0.0, margin_free=self.balance + fl, margin_level=None, leverage=100,
                               trade_mode=self.trade_mode, time=self.now, status="OK")

    def positions(self) -> list[PositionInfo]:
        self._mark()
        return list(self._pos.values())

    def deals(self, since_utc: float, until_utc: float) -> list[DealInfo]:
        return [d for d in self._deals if since_utc <= d.time <= until_utc]

    # ------------------------------------------------------------------ trading
    def _next_response(self) -> tuple[int, float]:
        return self.script.pop(0) if self.script else (RET_DONE, 0.0)

    def market_order(self, canonical, side, volume, sl, tp, deviation, magic, comment, filling) -> OrderResult:
        self.sent.append({"op": "open", "symbol": canonical, "side": side, "volume": volume, "sl": sl, "tp": tp,
                          "filling": filling, "magic": magic})
        if filling not in self.allowed_fillings:
            return OrderResult(False, RET_INVALID_FILL, comment="unsupported filling")
        code, slip = self._next_response()
        t = self.tick(canonical)
        if t is None:
            return OrderResult(False, RET_LOCAL_REJECT, comment="no tick")
        if code != RET_DONE:
            return OrderResult(False, code, comment="scripted", bid=t.bid, ask=t.ask)
        price = (t.ask + slip) if side == "buy" else (t.bid - slip)
        ticket = next(self._tickets)
        self._pos[ticket] = PositionInfo(ticket, canonical, side, volume, price, price, sl, tp, 0.0, magic=magic,
                                         comment=comment, time=self.now, tag="BOT" if magic == self.magic else "OTHER",
                                         account=self.name)
        self._deals.append(DealInfo(ticket, ticket, self.now, canonical, side, "in", volume, price, 0.0,
                                    magic=magic, position_id=ticket))
        return OrderResult(True, RET_DONE, price=price, volume=volume, order=ticket, deal=ticket, bid=t.bid, ask=t.ask,
                           filling=["FOK", "IOC", "RETURN"][filling])

    def close_position(self, ticket, volume, deviation, magic, filling, comment="") -> OrderResult:
        p = self._pos.get(int(ticket))
        if p is None:
            return OrderResult(False, RET_LOCAL_REJECT, comment="position not found")
        code, slip = self._next_response()
        if code != RET_DONE:
            return OrderResult(False, code, comment="scripted")
        self._mark()
        vol = min(volume, p.volume)
        price = p.price_current - slip if p.side == "buy" else p.price_current + slip
        spec = self.symbol_spec(p.symbol)
        diff = (price - p.price_open) * (1 if p.side == "buy" else -1)
        pnl = diff / spec.tick_size * spec.tick_value * vol
        self.balance += pnl
        self._deals.append(DealInfo(next(self._tickets), 0, self.now, p.symbol, "sell" if p.side == "buy" else "buy",
                                    "out", vol, price, pnl, magic=magic, position_id=p.ticket))
        p.volume = round(p.volume - vol, 8)
        if p.volume <= 1e-9:
            del self._pos[p.ticket]
        return OrderResult(True, RET_DONE, price=price, volume=vol, order=int(ticket))

    def modify_sltp(self, ticket, sl, tp) -> OrderResult:
        p = self._pos.get(int(ticket))
        if p is None:
            return OrderResult(False, RET_LOCAL_REJECT, comment="position not found")
        p.sl, p.tp = sl, tp
        return OrderResult(True, RET_DONE)
