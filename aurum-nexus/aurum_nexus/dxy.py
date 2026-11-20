"""US Dollar Index support.

Uses the broker's DXY symbol when present (DXY, USDX, DX.f, USDIDX, ...). Otherwise a synthetic index
with the official ICE formula:

    DXY = 50.14348112 x EURUSD^-0.576 x USDJPY^0.136 x GBPUSD^-0.119 x USDCAD^0.091
                      x USDSEK^0.042 x USDCHF^0.036

EURUSD and USDJPY are mandatory. Documented fallback for missing optional pairs: their factor is
dropped (exponent 0). The result is then flagged "partial": its absolute level is not comparable to the
official index, but its direction, structure, VWAP and correlation (what the rules use) remain valid.
High/low of synthetic bars are built from component extremes in the USD-strong / USD-weak direction
(upper/lower bounds of the true range).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from aurum_nexus.symbols import DXY_RELATION, canonical

ICE_CONST = 50.14348112
ICE_WEIGHTS = {"EURUSD": -0.576, "USDJPY": 0.136, "GBPUSD": -0.119, "USDCAD": 0.091, "USDSEK": 0.042,
               "USDCHF": 0.036}
MANDATORY = ("EURUSD", "USDJPY")


def synthetic_value(prices: dict[str, float]) -> tuple[float | None, list[str]]:
    """Index value from a dict of current prices. Returns (value, missing_optional_pairs)."""
    if any(not prices.get(p) for p in MANDATORY):
        return None, [p for p in MANDATORY if not prices.get(p)]
    val = ICE_CONST
    missing = []
    for pair, w in ICE_WEIGHTS.items():
        p = prices.get(pair)
        if not p or p <= 0:
            missing.append(pair)
            continue
        val *= p ** w
    return val, missing


def synthetic_bars(components: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, list[str]]:
    """Synthetic DXY OHLC bars on the common timestamps of the mandatory pairs."""
    for p in MANDATORY:
        if p not in components or components[p] is None or len(components[p]) == 0:
            return pd.DataFrame(), [p]
    base = components["EURUSD"][["time"]].merge(components["USDJPY"][["time"]], on="time")
    if len(base) == 0:
        return pd.DataFrame(), ["no common timestamps"]
    t = base["time"].to_numpy("int64")
    log_o = np.full(len(t), math.log(ICE_CONST))
    log_c = log_o.copy()
    log_hi = log_o.copy()
    log_lo = log_o.copy()
    vol = np.zeros(len(t))
    missing = []
    for pair, w in ICE_WEIGHTS.items():
        df = components.get(pair)
        if df is None or len(df) == 0:
            missing.append(pair)
            continue
        df = df.sort_values("time")
        idx = np.searchsorted(df["time"].to_numpy("int64"), t, side="right") - 1
        if (idx < 0).all():
            missing.append(pair)
            continue
        idx = np.clip(idx, 0, len(df) - 1)
        o = df["open"].to_numpy(float)[idx]
        c = df["close"].to_numpy(float)[idx]
        h = df["high"].to_numpy(float)[idx]
        lw = df["low"].to_numpy(float)[idx]
        log_o += w * np.log(o)
        log_c += w * np.log(c)
        # USD-strong extreme: for negative weight the pair's LOW raises the index
        log_hi += w * np.log(h if w > 0 else lw)
        log_lo += w * np.log(lw if w > 0 else h)
        vol += df["tick_volume"].to_numpy(float)[idx]
    o, c = np.exp(log_o), np.exp(log_c)
    hi = np.maximum.reduce([np.exp(log_hi), o, c])
    lo = np.minimum.reduce([np.exp(log_lo), o, c])
    out = pd.DataFrame({"time": t, "open": o, "high": hi, "low": lo, "close": c, "tick_volume": vol,
                        "real_volume": 0.0, "spread": 0.0})
    return out, missing


def find_broker_dxy(available: list[str], candidates: list[str]) -> str | None:
    upper = {a.upper(): a for a in available}
    for c in candidates:
        if c.upper() in upper:
            return upper[c.upper()]
    return None


def returns_correlation(a: pd.DataFrame, b: pd.DataFrame, n: int = 192) -> float | None:
    """Pearson correlation of log returns over the last n common bars (M15 192 bars = 2 days)."""
    if a is None or b is None or len(a) < 10 or len(b) < 10:
        return None
    m = a[["time", "close"]].merge(b[["time", "close"]], on="time", suffixes=("_a", "_b")).tail(n + 1)
    if len(m) < 30:
        return None
    ra = np.diff(np.log(m["close_a"].to_numpy(float)))
    rb = np.diff(np.log(m["close_b"].to_numpy(float)))
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def dxy_modifier(symbol: str, direction: str, state: dict | None, corr: float | None, cfg: dict) -> dict:
    """Score modifier / block from the DXY state for a trade on `symbol`.

    state keys: bias (bull|bear|neutral), below_vwap (bool|None), rejecting_resistance, rejecting_support,
                breaking_up, breaking_down, divergence (dict per symbol or None)
    Gold & EURUSD/GBPUSD are inverse to DXY, USDJPY/USDCAD/USDCHF direct.
    """
    rel = DXY_RELATION.get(canonical(symbol), 0)
    cap = float(cfg.get("max_modifier", 10))
    res = {"points": 0.0, "block": False, "notes": [], "relation": rel, "weight": 1.0}
    if rel == 0 or not state:
        res["notes"].append("DXY n/a" if not state else "no direct DXY relation")
        return res
    weight = 1.0
    if corr is not None and abs(corr) < float(cfg.get("decoupled_abs_corr", 0.3)):
        weight = 0.3
        res["notes"].append(f"DXY decoupled (corr {corr:+.2f})")
    elif corr is not None and np.sign(corr) != rel:
        weight = 0.5
        res["notes"].append(f"DXY correlation sign unusual ({corr:+.2f})")
    # usd_dir: +1 means trade benefits from a stronger USD
    usd_dir = rel * (1 if direction == "long" else -1)
    pts = 0.0
    bias = state.get("bias", "neutral")
    if bias == "bull":
        pts += 4 * usd_dir
    elif bias == "bear":
        pts -= 4 * usd_dir
    bv = state.get("below_vwap")
    if bv is True:
        pts -= 2 * usd_dir
    elif bv is False:
        pts += 2 * usd_dir
    if state.get("rejecting_resistance"):
        pts -= 3 * usd_dir
        res["notes"].append("DXY rejecting resistance")
    if state.get("rejecting_support"):
        pts += 3 * usd_dir
        res["notes"].append("DXY rejecting support")
    breaking = None
    if state.get("breaking_up"):
        breaking = "up"
        pts += 3 * usd_dir
    if state.get("breaking_down"):
        breaking = "down"
        pts -= 3 * usd_dir
    div = (state.get("divergence") or {}).get(canonical(symbol))
    if div:
        # SMT-style divergence: symbol and DXY fail to confirm each other -> strong signal for the reversal
        if (div == "bullish" and direction == "long") or (div == "bearish" and direction == "short"):
            pts += 4
            res["notes"].append(f"DXY divergence {div}")
    pts *= weight
    res["weight"] = weight
    res["points"] = float(max(-cap, min(cap, pts)))
    if breaking and cfg.get("block_on_dxy_breakout", True) and weight >= 1.0:
        against = (breaking == "up" and usd_dir < 0) or (breaking == "down" and usd_dir > 0)
        if against:
            res["block"] = True
            res["notes"].append(f"DXY breaking {breaking} against the trade")
    return res
