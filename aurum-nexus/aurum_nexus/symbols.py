"""Symbol universe: broker suffix resolution, currency legs, DXY relation and correlation map."""
from __future__ import annotations

import re

SYMBOL_LEGS = {
    "XAUUSD": ("XAU", "USD"), "XAGUSD": ("XAG", "USD"),
    "EURUSD": ("EUR", "USD"), "GBPUSD": ("GBP", "USD"), "AUDUSD": ("AUD", "USD"), "NZDUSD": ("NZD", "USD"),
    "USDJPY": ("USD", "JPY"), "USDCAD": ("USD", "CAD"), "USDCHF": ("USD", "CHF"), "USDSEK": ("USD", "SEK"),
    "EURJPY": ("EUR", "JPY"), "GBPJPY": ("GBP", "JPY"), "AUDJPY": ("AUD", "JPY"),
}

# Sign of expected correlation with DXY (USD strength): -1 inverse, +1 direct, 0 no direct relation
DXY_RELATION = {
    "XAUUSD": -1, "XAGUSD": -1, "EURUSD": -1, "GBPUSD": -1, "AUDUSD": -1, "NZDUSD": -1,
    "USDJPY": 1, "USDCAD": 1, "USDCHF": 1, "USDSEK": 1,
    "EURJPY": 0, "GBPJPY": 0, "AUDJPY": 0,
}

# Static prior correlation groups (used when live correlation is unavailable).
# Positive group members move together; sign gives direction relative to the group.
CORRELATION_GROUPS = {
    "usd_short": {"EURUSD": 1, "GBPUSD": 1, "AUDUSD": 1, "NZDUSD": 1, "XAUUSD": 1, "XAGUSD": 1,
                  "USDCHF": -1, "USDJPY": -1, "USDCAD": -1},
    "metals": {"XAUUSD": 1, "XAGUSD": 1},
    "jpy_short": {"USDJPY": 1, "EURJPY": 1, "GBPJPY": 1, "AUDJPY": 1},
    "commodity": {"AUDUSD": 1, "NZDUSD": 1, "AUDJPY": 1},
    "europe": {"EURUSD": 1, "GBPUSD": 1, "USDCHF": -1},
}

# Session preferences used by default profiles (documented in README)
ASIA_PAIRS = {"USDJPY", "EURJPY", "GBPJPY", "AUDJPY", "AUDUSD", "NZDUSD"}


def legs(symbol: str) -> tuple[str, str]:
    s = canonical(symbol)
    if s in SYMBOL_LEGS:
        return SYMBOL_LEGS[s]
    if len(s) >= 6:
        return s[:3], s[3:6]
    return s, ""


def canonical(broker_symbol: str) -> str:
    """Strip broker prefix/suffix decorations: 'XAUUSD.m' -> 'XAUUSD', 'XAUUSDm' -> 'XAUUSD'."""
    s = broker_symbol.upper()
    m = re.search(r"([A-Z]{6})", s)
    if m and m.group(1) in SYMBOL_LEGS:
        return m.group(1)
    return re.sub(r"[^A-Z]", "", s)[:6]


def resolve_broker_symbol(canonical_name: str, available: list[str], suffix_candidates: list[str] | None = None,
                          explicit: dict[str, str] | None = None) -> str | None:
    """Find the broker's name for a canonical symbol among available names."""
    if explicit and canonical_name in explicit:
        return explicit[canonical_name] if explicit[canonical_name] in available or not available else None
    avail = list(available)
    upper = {a.upper(): a for a in avail}
    for suf in suffix_candidates or [""]:
        cand = (canonical_name + suf).upper()
        if cand in upper:
            return upper[cand]
    # generic: any symbol whose letters start with the canonical name, shortest first
    hits = sorted([a for a in avail if canonical(a) == canonical_name and a.upper().startswith(canonical_name)], key=len)
    if hits:
        return hits[0]
    hits = sorted([a for a in avail if canonical(a) == canonical_name], key=len)
    return hits[0] if hits else None


def usd_direction(symbol: str, direction: str) -> int:
    """+1 if the trade is long USD, -1 if short USD, 0 if no USD leg."""
    b, q = legs(symbol)
    d = 1 if direction == "long" else -1
    if q == "USD":
        return -d
    if b == "USD":
        return d
    return 0


def currency_exposure(symbol: str, direction: str) -> dict[str, int]:
    b, q = legs(symbol)
    d = 1 if direction == "long" else -1
    out = {}
    if b:
        out[b] = d
    if q:
        out[q] = out.get(q, 0) - d
    return out


def prior_correlation(a: str, b: str) -> float:
    """Rough prior correlation sign*magnitude between two symbols from static groups."""
    a, b = canonical(a), canonical(b)
    if a == b:
        return 1.0
    best = 0.0
    for name, grp in CORRELATION_GROUPS.items():
        if a in grp and b in grp:
            mag = {"metals": 0.85, "jpy_short": 0.85, "europe": 0.8}.get(name, 0.7)
            val = mag * grp[a] * grp[b]
            if abs(val) > abs(best):
                best = val
    return best


def pip_size(symbol: str, point: float | None = None) -> float:
    s = canonical(symbol)
    if s.startswith("XAU"):
        return 0.1
    if s.startswith("XAG"):
        return 0.01
    if s.endswith("JPY"):
        return 0.01
    return 0.0001


def yahoo_ticker(symbol: str) -> str | None:
    s = canonical(symbol)
    table = {"XAUUSD": "GC=F", "XAGUSD": "SI=F", "USDJPY": "JPY=X", "USDCAD": "CAD=X", "USDCHF": "CHF=X",
             "USDSEK": "SEK=X"}
    if s in table:
        return table[s]
    if s in SYMBOL_LEGS:
        return f"{s}=X"
    return None


def oanda_instrument(symbol: str) -> str:
    b, q = legs(symbol)
    return f"{b}_{q}"


def swissquote_instrument(symbol: str) -> str:
    b, q = legs(symbol)
    return f"{b}/{q}"
