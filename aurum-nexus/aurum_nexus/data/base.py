"""Common adapter interface for price sources and trading back-ends.

Implementations:
  Mt5Adapter            - real MetaTrader 5 terminal (Windows, official MetaTrader5 package)
  OandaAdapter          - OANDA v20 REST/stream (secondary price source)
  FallbackPublicAdapter - keyless public quotes/history (PREVIEW ONLY, never for live trading)
  MockAdapter           - deterministic synthetic data (unit tests / explicit --simulate only)
  PaperBroker           - simulated execution on real prices (paper mode)
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from aurum_nexus.models import AccountSnapshot, DealInfo, OrderResult, PositionInfo, SymbolSpec, Tick

# Official MT5 trade return codes (MqlTradeResult.retcode)
RET_REQUOTE = 10004
RET_REJECT = 10006
RET_CANCEL = 10007
RET_PLACED = 10008
RET_DONE = 10009
RET_DONE_PARTIAL = 10010
RET_ERROR = 10011
RET_TIMEOUT = 10012
RET_INVALID = 10013
RET_INVALID_VOLUME = 10014
RET_INVALID_PRICE = 10015
RET_INVALID_STOPS = 10016
RET_TRADE_DISABLED = 10017
RET_MARKET_CLOSED = 10018
RET_NO_MONEY = 10019
RET_PRICE_CHANGED = 10020
RET_PRICE_OFF = 10021
RET_TOO_MANY_REQUESTS = 10024
RET_CONNECTION = 10031
RET_INVALID_FILL = 10030
RET_ONLY_REAL = 10032
RET_LIMIT_ORDERS = 10033
RET_LIMIT_VOLUME = 10034
RET_LOCAL_REJECT = -1  # our own pre-send rejection

RETRYABLE = {RET_REQUOTE, RET_PRICE_CHANGED, RET_PRICE_OFF, RET_TIMEOUT, RET_CONNECTION, RET_TOO_MANY_REQUESTS}
SUCCESS = {RET_DONE, RET_DONE_PARTIAL, RET_PLACED}

RETCODE_TEXT = {
    RET_REQUOTE: "requote", RET_REJECT: "rejected", RET_CANCEL: "cancelled", RET_PLACED: "placed",
    RET_DONE: "done", RET_DONE_PARTIAL: "partial", RET_ERROR: "error", RET_TIMEOUT: "timeout",
    RET_INVALID: "invalid request", RET_INVALID_VOLUME: "invalid volume", RET_INVALID_PRICE: "invalid price",
    RET_INVALID_STOPS: "invalid stops", RET_TRADE_DISABLED: "trade disabled", RET_MARKET_CLOSED: "market closed",
    RET_NO_MONEY: "no money", RET_PRICE_CHANGED: "price changed", RET_PRICE_OFF: "off quotes",
    RET_TOO_MANY_REQUESTS: "too many requests", RET_CONNECTION: "no connection", RET_INVALID_FILL: "invalid filling",
    RET_LOCAL_REJECT: "local reject",
}

FILLING_NAMES = ["FOK", "IOC", "RETURN"]  # ORDER_FILLING_FOK=0, IOC=1, RETURN=2


class AdapterError(Exception):
    pass


class MarketDataAdapter(ABC):
    kind: str = "base"
    is_preview: bool = False      # True for fallback public feeds: never tradable live
    label: str = ""

    def __init__(self) -> None:
        self.connected = False
        self.last_error = ""
        self.last_tick_time: dict[str, float] = {}

    @abstractmethod
    def connect(self) -> bool: ...

    def disconnect(self) -> None:
        self.connected = False

    @abstractmethod
    def resolve_symbol(self, canonical: str) -> str | None: ...

    @abstractmethod
    def symbol_spec(self, canonical: str) -> SymbolSpec | None: ...

    @abstractmethod
    def tick(self, canonical: str) -> Tick | None: ...

    @abstractmethod
    def bars(self, canonical: str, tf: str, count: int) -> pd.DataFrame: ...

    def bars_range(self, canonical: str, tf: str, start: float, end: float) -> pd.DataFrame:
        b = self.bars(canonical, tf, 5000)
        return b[(b["time"] >= start) & (b["time"] < end)].reset_index(drop=True)

    def has_volume(self, canonical: str) -> bool:
        return True

    def market_book(self, canonical: str) -> list[dict] | None:
        return None

    def status(self) -> dict:
        return {"kind": self.kind, "connected": self.connected, "preview": self.is_preview,
                "label": self.label, "error": self.last_error}


class TradingAdapter(MarketDataAdapter):
    """A market-data adapter that can also report an account and execute orders."""

    @abstractmethod
    def account(self) -> AccountSnapshot: ...

    @abstractmethod
    def positions(self) -> list[PositionInfo]: ...

    @abstractmethod
    def deals(self, since_utc: float, until_utc: float) -> list[DealInfo]: ...

    @abstractmethod
    def market_order(self, canonical: str, side: str, volume: float, sl: float, tp: float, deviation: int,
                     magic: int, comment: str, filling: int) -> OrderResult: ...

    @abstractmethod
    def close_position(self, ticket: int, volume: float, deviation: int, magic: int, filling: int,
                       comment: str = "") -> OrderResult: ...

    @abstractmethod
    def modify_sltp(self, ticket: int, sl: float, tp: float) -> OrderResult: ...
