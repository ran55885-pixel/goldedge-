"""Generic contract specifications used when no MT5 terminal provides symbol_info
(paper accounts on OANDA/fallback data, backtests). Standard retail CFD conventions:
XAU 100 oz/lot, XAG 5000 oz/lot, FX 100,000 base units/lot; volume 0.01..100 step 0.01.
tick_value is converted into the account currency with live conversion rates when available.
"""
from __future__ import annotations

from typing import Callable

from aurum_nexus.models import SymbolSpec
from aurum_nexus.symbols import canonical, legs

CONTRACT = {"XAU": 100.0, "XAG": 5000.0}


def digits_for(symbol: str) -> int:
    s = canonical(symbol)
    if s.startswith("XAU"):
        return 2
    if s.startswith("XAG"):
        return 3
    if s.endswith("JPY"):
        return 3
    return 5


def generic_spec(symbol: str, account_ccy: str = "USD",
                 convert: Callable[[str, str], float | None] | None = None) -> SymbolSpec:
    s = canonical(symbol)
    base, quote = legs(s)
    d = digits_for(s)
    point = 10.0 ** -d
    contract = CONTRACT.get(base, 100000.0)
    tick_value_quote = point * contract  # value per tick in quote currency for 1 lot
    rate = 1.0
    if quote != account_ccy:
        r = convert(quote, account_ccy) if convert else None
        rate = r if r else 1.0
    return SymbolSpec(name=s, broker_name=s, digits=d, point=point, tick_size=point,
                      tick_value=tick_value_quote * rate, tick_value_profit=tick_value_quote * rate,
                      contract_size=contract, volume_min=0.01, volume_max=100.0, volume_step=0.01,
                      filling_mode=3, stops_level=0, currency_base=base, currency_profit=quote,
                      currency_margin=base, spread_points=0.0)


def make_converter(price_of: Callable[[str], float | None]) -> Callable[[str, str], float | None]:
    """Build a currency converter from a mid-price lookup by canonical symbol (e.g. 'USDJPY')."""
    def convert(frm: str, to: str) -> float | None:
        if frm == to:
            return 1.0
        p = price_of(frm + to)
        if p:
            return p
        p = price_of(to + frm)
        if p:
            return 1.0 / p
        # via USD
        if frm != "USD" and to != "USD":
            a = convert(frm, "USD")
            b = convert("USD", to)
            if a and b:
                return a * b
        return None
    return convert
