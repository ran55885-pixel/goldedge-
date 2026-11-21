"""Real MetaTrader 5 adapter (official `MetaTrader5` Python package, Windows only).

One MT5 terminal per process: each account worker owns exactly one Mt5Adapter bound to its own
portable terminal installation (terminal_path). Server times returned by MT5 are converted to UTC
using the account's server_utc_offset_hours.

Field names used here are the official ones (account_info, symbol_info, symbol_info_tick,
copy_rates_from_pos, positions_get, history_deals_get, order_send, market_book_*). They are verified
in tests against a strict fake module that only exposes the documented fields.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from aurum_nexus.data.bars import normalize
from aurum_nexus.data.base import (RET_DONE, RET_LOCAL_REJECT, SUCCESS, TradingAdapter)
from aurum_nexus.models import AccountSnapshot, DealInfo, OrderResult, PositionInfo, SymbolSpec, Tick, empty_bars
from aurum_nexus.symbols import resolve_broker_symbol
from aurum_nexus.timeutil import server_to_utc

TF_ATTR = {"M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15", "M30": "TIMEFRAME_M30",
           "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4", "D1": "TIMEFRAME_D1", "W1": "TIMEFRAME_W1"}
TRADE_MODE = {0: "DEMO", 1: "CONTEST", 2: "REAL"}


def import_mt5():
    try:
        import MetaTrader5 as mt5  # type: ignore
        return mt5
    except ImportError:
        return None


class Mt5Adapter(TradingAdapter):
    kind = "mt5"
    is_preview = False

    def __init__(self, account_cfg, mt5_module=None, suffix_candidates: list[str] | None = None,
                 default_offset_hours: float = 2.0, bot_magic: int = 770077):
        super().__init__()
        self.acc = account_cfg
        self.mt5 = mt5_module if mt5_module is not None else import_mt5()
        self.offset = account_cfg.server_utc_offset_hours if account_cfg.server_utc_offset_hours is not None \
            else default_offset_hours
        self.suffixes = suffix_candidates or [""]
        self.bot_magic = account_cfg.magic or bot_magic
        self._symbols_cache: list[str] | None = None
        self._resolved: dict[str, str | None] = {}
        self.label = f"MT5 · {account_cfg.name}"
        self.book_subscribed: set[str] = set()

    # ------------------------------------------------------------------ connection
    def connect(self) -> bool:
        if self.mt5 is None:
            self.last_error = "MetaTrader5 package not installed (Windows only)"
            self.connected = False
            return False
        kwargs = {}
        if self.acc.terminal_path:
            kwargs["path"] = self.acc.terminal_path
            kwargs["portable"] = True
        if self.acc.login:
            kwargs["login"] = int(self.acc.login)
        pw = self.acc.password()
        if pw:
            kwargs["password"] = pw
        if self.acc.server:
            kwargs["server"] = self.acc.server
        kwargs["timeout"] = 20000
        ok = bool(self.mt5.initialize(**kwargs))
        if not ok:
            err = self.mt5.last_error()
            self.last_error = f"initialize failed: {err}"
            self.connected = False
            return False
        info = self.mt5.account_info()
        if info is None:
            self.last_error = f"account_info None: {self.mt5.last_error()}"
            self.connected = False
            return False
        if self.acc.login and int(info.login) != int(self.acc.login):
            self.last_error = f"terminal logged into {info.login}, expected {self.acc.login}"
            self.mt5.shutdown()
            self.connected = False
            return False
        self.connected = True
        self.last_error = ""
        self._symbols_cache = None
        return True

    def disconnect(self) -> None:
        if self.mt5 is not None:
            try:
                for s in list(self.book_subscribed):
                    self.mt5.market_book_release(s)
                self.mt5.shutdown()
            except Exception:
                pass
        self.connected = False

    def ping(self) -> bool:
        if self.mt5 is None:
            return False
        ti = self.mt5.terminal_info()
        ok = ti is not None and bool(getattr(ti, "connected", False))
        if not ok:
            self.connected = False
        return ok

    # ------------------------------------------------------------------ symbols
    def _all_symbols(self) -> list[str]:
        if self._symbols_cache is None:
            syms = self.mt5.symbols_get() or ()
            self._symbols_cache = [s.name for s in syms]
        return self._symbols_cache

    def resolve_symbol(self, canonical: str) -> str | None:
        if canonical in self._resolved:
            return self._resolved[canonical]
        name = resolve_broker_symbol(canonical, self._all_symbols(), self.suffixes, self.acc.symbol_map or None)
        if name:
            self.mt5.symbol_select(name, True)
        self._resolved[canonical] = name
        return name

    def symbol_spec(self, canonical: str) -> SymbolSpec | None:
        name = self.resolve_symbol(canonical)
        if not name:
            return None
        si = self.mt5.symbol_info(name)
        if si is None:
            return None
        tv_loss = getattr(si, "trade_tick_value_loss", 0.0) or si.trade_tick_value
        return SymbolSpec(
            name=canonical, broker_name=name, digits=int(si.digits), point=float(si.point),
            tick_size=float(si.trade_tick_size), tick_value=float(tv_loss),
            tick_value_profit=float(getattr(si, "trade_tick_value_profit", 0.0) or si.trade_tick_value),
            contract_size=float(si.trade_contract_size), volume_min=float(si.volume_min),
            volume_max=float(si.volume_max), volume_step=float(si.volume_step), filling_mode=int(si.filling_mode),
            stops_level=int(si.trade_stops_level), currency_base=si.currency_base, currency_profit=si.currency_profit,
            currency_margin=si.currency_margin, spread_points=float(si.spread),
        )

    # ------------------------------------------------------------------ market data
    def tick(self, canonical: str) -> Tick | None:
        name = self.resolve_symbol(canonical)
        if not name:
            return None
        t = self.mt5.symbol_info_tick(name)
        if t is None or (t.bid == 0 and t.ask == 0):
            return None
        ts = (t.time_msc / 1000.0) if getattr(t, "time_msc", 0) else float(t.time)
        utc = server_to_utc(ts, self.offset)
        self.last_tick_time[canonical] = utc
        return Tick(canonical, utc, float(t.bid), float(t.ask), float(t.last), float(t.volume), source=self.label)

    def bars(self, canonical: str, tf: str, count: int) -> pd.DataFrame:
        name = self.resolve_symbol(canonical)
        if not name:
            return empty_bars()
        rates = self.mt5.copy_rates_from_pos(name, getattr(self.mt5, TF_ATTR[tf]), 0, int(count))
        return self._rates_to_df(rates)

    def bars_range(self, canonical: str, tf: str, start: float, end: float) -> pd.DataFrame:
        name = self.resolve_symbol(canonical)
        if not name:
            return empty_bars()
        f = datetime.fromtimestamp(start + self.offset * 3600, tz=timezone.utc)
        t = datetime.fromtimestamp(end + self.offset * 3600, tz=timezone.utc)
        rates = self.mt5.copy_rates_range(name, getattr(self.mt5, TF_ATTR[tf]), f, t)
        return self._rates_to_df(rates)

    def _rates_to_df(self, rates) -> pd.DataFrame:
        if rates is None or len(rates) == 0:
            return empty_bars()
        arr = np.asarray(rates)
        df = pd.DataFrame({
            "time": (arr["time"].astype("int64") - int(self.offset * 3600)),
            "open": arr["open"], "high": arr["high"], "low": arr["low"], "close": arr["close"],
            "tick_volume": arr["tick_volume"], "real_volume": arr["real_volume"], "spread": arr["spread"],
        })
        return normalize(df)

    def has_volume(self, canonical: str) -> bool:
        return True  # tick_volume is always present in MT5 (an estimate for OTC FX/gold)

    def market_book(self, canonical: str) -> list[dict] | None:
        name = self.resolve_symbol(canonical)
        if not name:
            return None
        if name not in self.book_subscribed:
            if not self.mt5.market_book_add(name):
                return None
            self.book_subscribed.add(name)
        book = self.mt5.market_book_get(name)
        if not book:
            return None
        out = []
        for b in book:
            side = "sell" if b.type in (1, 3) else "buy"   # BOOK_TYPE_SELL=1, BUY=2, SELL_MARKET=3, BUY_MARKET=4
            out.append({"side": side, "price": float(b.price), "volume": float(b.volume_dbl or b.volume)})
        return out

    # ------------------------------------------------------------------ account
    def account(self) -> AccountSnapshot:
        a = self.mt5.account_info()
        if a is None:
            self.connected = False
            return AccountSnapshot(name=self.acc.name, status="DISCONNECTED", error=str(self.mt5.last_error()),
                                   time=time.time())
        return AccountSnapshot(
            name=self.acc.name, login=int(a.login), server=a.server, company=a.company, holder=a.name,
            currency=a.currency, balance=float(a.balance), equity=float(a.equity), profit=float(a.profit),
            margin=float(a.margin), margin_free=float(a.margin_free),
            margin_level=float(a.margin_level) if a.margin else None, leverage=int(a.leverage),
            trade_mode=TRADE_MODE.get(int(a.trade_mode), "REAL"), time=time.time(), status="OK",
        )

    def _tag(self, magic: int) -> str:
        if magic == self.bot_magic:
            return "BOT"
        if magic == 0:
            return "MANUAL"
        return "OTHER"

    def positions(self) -> list[PositionInfo]:
        ps = self.mt5.positions_get()
        if ps is None:
            return []
        out = []
        for p in ps:
            out.append(PositionInfo(
                ticket=int(p.ticket), symbol=p.symbol, side="buy" if int(p.type) == 0 else "sell",
                volume=float(p.volume), price_open=float(p.price_open), price_current=float(p.price_current),
                sl=float(p.sl), tp=float(p.tp), profit=float(p.profit), swap=float(p.swap), magic=int(p.magic),
                comment=p.comment, time=server_to_utc(p.time, self.offset), tag=self._tag(int(p.magic)),
                account=self.acc.name,
            ))
        return out

    def deals(self, since_utc: float, until_utc: float) -> list[DealInfo]:
        # history_deals_get interprets datetimes as server time; widen by a day and filter precisely.
        f = datetime.fromtimestamp(since_utc + self.offset * 3600 - 86400, tz=timezone.utc)
        t = datetime.fromtimestamp(until_utc + self.offset * 3600 + 86400, tz=timezone.utc)
        ds = self.mt5.history_deals_get(f, t)
        if ds is None:
            return []
        out = []
        for d in ds:
            utc = server_to_utc(d.time, self.offset)
            if utc < since_utc or utc > until_utc:
                continue
            typ = {0: "buy", 1: "sell", 2: "balance"}.get(int(d.type), "other")
            entry = {0: "in", 1: "out", 2: "inout", 3: "out_by"}.get(int(d.entry), "in")
            out.append(DealInfo(
                ticket=int(d.ticket), order=int(d.order), time=utc, symbol=d.symbol, type=typ, entry=entry,
                volume=float(d.volume), price=float(d.price), profit=float(d.profit),
                commission=float(d.commission), swap=float(d.swap), fee=float(getattr(d, "fee", 0.0)),
                magic=int(d.magic), position_id=int(d.position_id), comment=d.comment,
            ))
        return out

    # ------------------------------------------------------------------ trading
    def _result(self, r, filling: int) -> OrderResult:
        if r is None:
            code, msg = self.mt5.last_error()
            return OrderResult(False, RET_LOCAL_REJECT, comment=f"order_send None: {code} {msg}")
        return OrderResult(ok=int(r.retcode) in SUCCESS, retcode=int(r.retcode), price=float(r.price),
                           volume=float(r.volume), order=int(r.order), deal=int(r.deal), comment=str(r.comment),
                           bid=float(r.bid), ask=float(r.ask), filling=["FOK", "IOC", "RETURN"][filling])

    def market_order(self, canonical, side, volume, sl, tp, deviation, magic, comment, filling) -> OrderResult:
        name = self.resolve_symbol(canonical)
        if not name:
            return OrderResult(False, RET_LOCAL_REJECT, comment="symbol not available")
        t = self.mt5.symbol_info_tick(name)
        if t is None:
            return OrderResult(False, RET_LOCAL_REJECT, comment="no tick")
        req = {
            "action": self.mt5.TRADE_ACTION_DEAL, "symbol": name, "volume": float(volume),
            "type": self.mt5.ORDER_TYPE_BUY if side == "buy" else self.mt5.ORDER_TYPE_SELL,
            "price": float(t.ask if side == "buy" else t.bid), "sl": float(sl or 0.0), "tp": float(tp or 0.0),
            "deviation": int(deviation), "magic": int(magic), "comment": comment[:31],
            "type_time": self.mt5.ORDER_TIME_GTC, "type_filling": int(filling),
        }
        return self._result(self.mt5.order_send(req), filling)

    def close_position(self, ticket, volume, deviation, magic, filling, comment="") -> OrderResult:
        ps = self.mt5.positions_get(ticket=int(ticket))
        if not ps:
            return OrderResult(False, RET_LOCAL_REJECT, comment="position not found")
        p = ps[0]
        t = self.mt5.symbol_info_tick(p.symbol)
        if t is None:
            return OrderResult(False, RET_LOCAL_REJECT, comment="no tick")
        is_buy = int(p.type) == 0
        req = {
            "action": self.mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": float(min(volume, p.volume)),
            "type": self.mt5.ORDER_TYPE_SELL if is_buy else self.mt5.ORDER_TYPE_BUY, "position": int(ticket),
            "price": float(t.bid if is_buy else t.ask), "deviation": int(deviation), "magic": int(magic),
            "comment": (comment or "aurum close")[:31], "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": int(filling),
        }
        return self._result(self.mt5.order_send(req), filling)

    def modify_sltp(self, ticket, sl, tp) -> OrderResult:
        ps = self.mt5.positions_get(ticket=int(ticket))
        if not ps:
            return OrderResult(False, RET_LOCAL_REJECT, comment="position not found")
        req = {"action": self.mt5.TRADE_ACTION_SLTP, "symbol": ps[0].symbol, "position": int(ticket),
               "sl": float(sl or 0.0), "tp": float(tp or 0.0)}
        r = self.mt5.order_send(req)
        res = self._result(r, 2)
        if r is not None and int(r.retcode) == RET_DONE:
            res.ok = True
        return res
