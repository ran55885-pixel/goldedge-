"""STRICT fake of the official `MetaTrader5` Python package.

Only documented attribute names exist (namedtuples raise AttributeError on anything else) and order_send
rejects unknown MqlTradeRequest keys, so the real Mt5Adapter's field mapping is verified against the
official API surface. Values are simulated test data.
"""
from __future__ import annotations

from collections import namedtuple

import numpy as np

# --- official structures (field names as documented by MetaQuotes) -------------------------------------
AccountInfo = namedtuple("AccountInfo", [
    "login", "trade_mode", "leverage", "limit_orders", "margin_so_mode", "trade_allowed", "trade_expert",
    "margin_mode", "currency_digits", "fifo_close", "balance", "credit", "profit", "equity", "margin",
    "margin_free", "margin_level", "margin_so_call", "margin_so_so", "margin_initial", "margin_maintenance",
    "assets", "liabilities", "commission_blocked", "name", "server", "currency", "company"])
SymbolInfo = namedtuple("SymbolInfo", [
    "custom", "chart_mode", "select", "visible", "session_deals", "session_buy_orders", "session_sell_orders",
    "volume", "volumehigh", "volumelow", "time", "digits", "spread", "spread_float", "ticks_bookdepth",
    "trade_calc_mode", "trade_mode", "start_time", "expiration_time", "trade_stops_level", "trade_freeze_level",
    "trade_exemode", "swap_mode", "swap_rollover3days", "margin_hedged_use_leg", "expiration_mode", "filling_mode",
    "order_mode", "order_gtc_mode", "option_mode", "option_right", "bid", "bidhigh", "bidlow", "ask", "askhigh",
    "asklow", "last", "lasthigh", "lastlow", "volume_real", "point", "trade_tick_value", "trade_tick_value_profit",
    "trade_tick_value_loss", "trade_tick_size", "trade_contract_size", "trade_accrued_interest", "trade_face_value",
    "trade_liquidity_rate", "volume_min", "volume_max", "volume_step", "volume_limit", "swap_long", "swap_short",
    "margin_initial", "margin_maintenance", "currency_base", "currency_profit", "currency_margin", "bank",
    "description", "exchange", "formula", "isin", "name", "page", "path"])
Tick = namedtuple("Tick", ["time", "bid", "ask", "last", "volume", "time_msc", "flags", "volume_real"])
TradePosition = namedtuple("TradePosition", [
    "ticket", "time", "time_msc", "time_update", "time_update_msc", "type", "magic", "identifier", "reason",
    "volume", "price_open", "sl", "tp", "price_current", "swap", "profit", "symbol", "comment", "external_id"])
TradeDeal = namedtuple("TradeDeal", [
    "ticket", "order", "time", "time_msc", "type", "entry", "magic", "position_id", "reason", "volume", "price",
    "commission", "swap", "profit", "fee", "symbol", "comment", "external_id"])
OrderSendResult = namedtuple("OrderSendResult", ["retcode", "deal", "order", "volume", "price", "bid", "ask",
                                                 "comment", "request_id", "retcode_external", "request"])
TerminalInfo = namedtuple("TerminalInfo", ["community_account", "community_connection", "connected", "dlls_allowed",
                                           "trade_allowed", "tradeapi_disabled", "email_enabled", "ftp_enabled",
                                           "notifications_enabled", "mqid", "build", "maxbars", "codepage",
                                           "ping_last", "community_balance", "retransmission", "company", "name",
                                           "language", "path", "data_path", "commondata_path"])
BookInfo = namedtuple("BookInfo", ["type", "price", "volume", "volume_dbl"])

RATES_DTYPE = np.dtype([("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"),
                        ("tick_volume", "<u8"), ("spread", "<i4"), ("real_volume", "<u8")])

REQUEST_KEYS = {"action", "magic", "order", "symbol", "volume", "price", "stoplimit", "sl", "tp", "deviation", "type",
                "type_filling", "type_time", "expiration", "comment", "position", "position_by"}


class FakeMT5:
    # constants (official values)
    TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M15, TIMEFRAME_M30 = 1, 5, 15, 30
    TIMEFRAME_H1, TIMEFRAME_H4, TIMEFRAME_D1, TIMEFRAME_W1 = 16385, 16388, 16408, 32769
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    TRADE_ACTION_DEAL, TRADE_ACTION_SLTP = 1, 6
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    TRADE_RETCODE_DONE = 10009

    def __init__(self, server_offset_h: float = 2.0, login: int = 5550001, trade_mode: int = 0):
        self.offset = server_offset_h
        self.login = login
        self.trade_mode = trade_mode
        self.initialized = False
        self.init_kwargs = {}
        self.sent: list[dict] = []
        self.positions: list[TradePosition] = []
        self.deals: list[TradeDeal] = []
        self.next_retcode: list[int] = []
        self.book_added: set = set()
        self.symbols = {
            "XAUUSD.m": self._sym("XAUUSD.m", 2, 0.01, 1.0, 0.01, 100, "XAU", "USD", 2350.10, 2350.35, filling=1),
            "EURUSD.m": self._sym("EURUSD.m", 5, 0.00001, 1.0, 0.00001, 100000, "EUR", "USD", 1.08500, 1.08508, filling=2),
            "USDJPY.m": self._sym("USDJPY.m", 3, 0.001, 0.6667, 0.001, 100000, "USD", "JPY", 150.000, 150.012, filling=3),
        }

    @staticmethod
    def _sym(name, digits, point, tv, ts, contract, base, quote, bid, ask, filling):
        vals = {f: 0 for f in SymbolInfo._fields}
        vals.update(name=name, digits=digits, point=point, trade_tick_value=tv, trade_tick_value_profit=tv,
                    trade_tick_value_loss=tv, trade_tick_size=ts, trade_contract_size=contract, volume_min=0.01,
                    volume_max=50.0, volume_step=0.01, filling_mode=filling, trade_stops_level=10, currency_base=base,
                    currency_profit=quote, currency_margin=base, spread=int(round((ask - bid) / point)), bid=bid,
                    ask=ask, visible=True, select=True, description=name, path=f"Forex\\{name}", bank="", exchange="",
                    formula="", isin="", page="")
        return SymbolInfo(**vals)

    # --- lifecycle ---------------------------------------------------------------------------------------
    def initialize(self, path=None, **kwargs):
        allowed = {"login", "password", "server", "timeout", "portable"}
        bad = set(kwargs) - allowed
        if bad:
            raise TypeError(f"initialize() got unexpected keyword(s) {bad}")
        self.init_kwargs = {"path": path, **kwargs}
        self.initialized = True
        return True

    def shutdown(self):
        self.initialized = False

    def last_error(self):
        return (1, "Success")

    def terminal_info(self):
        vals = {f: 0 for f in TerminalInfo._fields}
        vals.update(connected=self.initialized, trade_allowed=True, name="MetaTrader 5", company="Fake Broker",
                    path="C:/MT5", data_path="C:/MT5", commondata_path="C:/MT5")
        return TerminalInfo(**vals) if self.initialized else None

    def account_info(self):
        if not self.initialized:
            return None
        fl = sum(p.profit for p in self.positions)
        return AccountInfo(login=self.login, trade_mode=self.trade_mode, leverage=500, limit_orders=200,
                           margin_so_mode=0, trade_allowed=True, trade_expert=True, margin_mode=2, currency_digits=2,
                           fifo_close=False, balance=10000.0, credit=0.0, profit=fl, equity=10000.0 + fl,
                           margin=120.0, margin_free=9880.0 + fl, margin_level=8333.3, margin_so_call=50.0,
                           margin_so_so=30.0, margin_initial=0.0, margin_maintenance=0.0, assets=0.0, liabilities=0.0,
                           commission_blocked=0.0, name="Test Holder", server="FakeBroker-Demo", currency="USD",
                           company="Fake Broker Ltd")

    # --- symbols / prices --------------------------------------------------------------------------------
    def symbols_get(self, group=None):
        return tuple(self.symbols.values())

    def symbol_select(self, name, enable=True):
        return name in self.symbols

    def symbol_info(self, name):
        return self.symbols.get(name)

    def symbol_info_tick(self, name):
        s = self.symbols.get(name)
        if s is None:
            return None
        t = 1_700_000_000 + int(self.offset * 3600)
        return Tick(time=t, bid=s.bid, ask=s.ask, last=0.0, volume=0, time_msc=t * 1000 + 250, flags=6, volume_real=0.0)

    def copy_rates_from_pos(self, name, timeframe, start_pos, count):
        if name not in self.symbols:
            return None
        n = min(count, 50)
        arr = np.zeros(n, dtype=RATES_DTYPE)
        base = 1_700_000_000 + int(self.offset * 3600)
        for i in range(n):
            arr[i] = (base + i * 60, 1.0 + i, 2.0 + i, 0.5 + i, 1.5 + i, 100 + i, 3, 0)
        return arr

    def copy_rates_range(self, name, timeframe, date_from, date_to):
        return self.copy_rates_from_pos(name, timeframe, 0, 10)

    # --- account state -----------------------------------------------------------------------------------
    def positions_get(self, symbol=None, group=None, ticket=None):
        ps = self.positions
        if ticket is not None:
            ps = [p for p in ps if p.ticket == ticket]
        return tuple(ps)

    def history_deals_get(self, date_from, date_to, group=None):
        self.last_deals_range = (date_from, date_to)
        return tuple(self.deals)

    def market_book_add(self, symbol):
        self.book_added.add(symbol)
        return True

    def market_book_get(self, symbol):
        if symbol not in self.book_added:
            return None
        return (BookInfo(1, 2350.50, 5, 5.0), BookInfo(1, 2350.40, 9, 9.0), BookInfo(2, 2350.10, 20, 20.0),
                BookInfo(2, 2350.00, 7, 7.0))

    def market_book_release(self, symbol):
        self.book_added.discard(symbol)
        return True

    # --- trading -----------------------------------------------------------------------------------------
    def order_send(self, request: dict):
        bad = set(request) - REQUEST_KEYS
        if bad:
            raise KeyError(f"unknown MqlTradeRequest fields {bad}")
        self.sent.append(dict(request))
        code = self.next_retcode.pop(0) if self.next_retcode else 10009
        price = request.get("price", 0.0)
        return OrderSendResult(retcode=code, deal=9001 if code == 10009 else 0, order=8001 if code == 10009 else 0,
                               volume=request.get("volume", 0.0), price=price, bid=price, ask=price,
                               comment="Request executed" if code == 10009 else "Requote", request_id=1,
                               retcode_external=0, request=None)
