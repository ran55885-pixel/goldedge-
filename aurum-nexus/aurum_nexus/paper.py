"""PaperBroker - simulated execution on REAL prices (the account's own MT5 quotes when its terminal is
connected, otherwise the analysis feed). Fills at ask (buy) / bid (sell). All figures are labelled PAPER."""
from __future__ import annotations

import itertools
import time
from typing import Callable

from aurum_nexus.data.base import RET_DONE, RET_LOCAL_REJECT, TradingAdapter
from aurum_nexus.models import AccountSnapshot, DealInfo, OrderResult, PositionInfo, SymbolSpec, Tick


class PaperBroker(TradingAdapter):
    kind = "paper"
    is_preview = False

    def __init__(self, name: str, balance: float, currency: str, price_fn: Callable[[str], Tick | None],
                 spec_fn: Callable[[str], SymbolSpec | None], clock: Callable[[], float] = time.time,
                 slippage_points: float = 0.0, magic: int = 770077):
        super().__init__()
        self.name = name
        self.balance = float(balance)
        self.start_balance = float(balance)
        self.currency = currency
        self.price_fn = price_fn
        self.spec_fn = spec_fn
        self.clock = clock
        self.slippage_points = slippage_points
        self.commission_per_lot = 0.0      # round-turn commission per lot, charged on close (backtests)
        self.magic = magic
        self.pos: dict[int, PositionInfo] = {}
        self.deal_log: list[DealInfo] = []
        self._ids = itertools.count(1)
        self.connected = True
        self.label = f"PAPER · {name}"

    # market data passthrough (not used for analysis)
    def connect(self) -> bool:
        return True

    def resolve_symbol(self, canonical: str) -> str | None:
        return canonical

    def symbol_spec(self, canonical: str) -> SymbolSpec | None:
        return self.spec_fn(canonical)

    def tick(self, canonical: str) -> Tick | None:
        return self.price_fn(canonical)

    def bars(self, canonical, tf, count):
        raise NotImplementedError("PaperBroker has no bars")

    # ------------------------------------------------------------------ accounting
    def _pnl(self, p: PositionInfo, price: float, volume: float) -> float:
        spec = self.spec_fn(p.symbol)
        if spec is None:
            return 0.0
        diff = (price - p.price_open) * (1 if p.side == "buy" else -1)
        tv = spec.tick_value_profit if (diff > 0 and spec.tick_value_profit) else spec.tick_value
        return diff / spec.tick_size * tv * volume

    def mark(self) -> None:
        for p in self.pos.values():
            t = self.price_fn(p.symbol)
            if t is None:
                continue
            px = t.bid if p.side == "buy" else t.ask
            p.price_current = px
            p.profit = self._pnl(p, px, p.volume)

    def account(self) -> AccountSnapshot:
        self.mark()
        fl = sum(p.profit for p in self.pos.values())
        return AccountSnapshot(name=self.name, login=None, server="PAPER", company="Aurum Nexus paper",
                               holder="PAPER", currency=self.currency, balance=self.balance, equity=self.balance + fl,
                               profit=fl, margin=None, margin_free=None, margin_level=None, leverage=None,
                               trade_mode="PAPER", time=self.clock(), status="OK")

    def positions(self) -> list[PositionInfo]:
        self.mark()
        return list(self.pos.values())

    def deals(self, since_utc: float, until_utc: float) -> list[DealInfo]:
        return [d for d in self.deal_log if since_utc <= d.time <= until_utc]

    # ------------------------------------------------------------------ trading
    def market_order(self, canonical, side, volume, sl, tp, deviation, magic, comment, filling) -> OrderResult:
        t = self.price_fn(canonical)
        spec = self.spec_fn(canonical)
        if t is None or spec is None:
            return OrderResult(False, RET_LOCAL_REJECT, comment="no price")
        slip = self.slippage_points * spec.point
        price = t.ask + slip if side == "buy" else t.bid - slip
        ticket = next(self._ids)
        self.pos[ticket] = PositionInfo(ticket, canonical, side, float(volume), price, price, float(sl or 0),
                                        float(tp or 0), 0.0, magic=magic, comment=comment, time=self.clock(),
                                        tag="PAPER", account=self.name)
        self.deal_log.append(DealInfo(ticket, ticket, self.clock(), canonical, side, "in", float(volume), price, 0.0,
                                      magic=magic, position_id=ticket))
        return OrderResult(True, RET_DONE, price=price, volume=float(volume), order=ticket, deal=ticket, bid=t.bid,
                           ask=t.ask, filling="PAPER")

    def close_position(self, ticket, volume, deviation, magic, filling, comment="", price: float | None = None) -> OrderResult:
        p = self.pos.get(int(ticket))
        if p is None:
            return OrderResult(False, RET_LOCAL_REJECT, comment="position not found")
        t = self.price_fn(p.symbol)
        if price is None:
            if t is None:
                return OrderResult(False, RET_LOCAL_REJECT, comment="no price")
            price = t.bid if p.side == "buy" else t.ask
        vol = min(float(volume), p.volume)
        pnl = self._pnl(p, price, vol)
        comm = self.commission_per_lot * vol
        self.balance += pnl - comm
        self.deal_log.append(DealInfo(next(self._ids), 0, self.clock(), p.symbol, "sell" if p.side == "buy" else "buy",
                                      "out", vol, price, pnl, commission=-comm, magic=magic, position_id=p.ticket,
                                      comment=comment))
        p.volume = round(p.volume - vol, 8)
        if p.volume <= 1e-9:
            del self.pos[p.ticket]
        return OrderResult(True, RET_DONE, price=price, volume=vol, order=int(ticket), filling="PAPER")

    def modify_sltp(self, ticket, sl, tp) -> OrderResult:
        p = self.pos.get(int(ticket))
        if p is None:
            return OrderResult(False, RET_LOCAL_REJECT, comment="position not found")
        p.sl, p.tp = float(sl or 0), float(tp or 0)
        return OrderResult(True, RET_DONE)

    def restore(self, open_trades: list[dict]) -> None:
        """Rebuild paper positions from the persisted trade book after a restart."""
        for t in open_trades:
            tk = int(t.get("ticket") or next(self._ids))
            self.pos[tk] = PositionInfo(tk, t["symbol"], "buy" if t["direction"] == "long" else "sell",
                                        float(t["volume_open"]), float(t["entry"]), float(t["entry"]), float(t["stop"]),
                                        float(t["tp2"] or 0), 0.0, magic=self.magic, time=float(t["open_ts"]),
                                        tag="PAPER", account=self.name)
            self._ids = itertools.count(max(self.pos) + 1)
