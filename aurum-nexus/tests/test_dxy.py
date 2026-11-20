import math

import numpy as np
import pandas as pd
import pytest

from conftest import T0, path_bars
from aurum_nexus import dxy


def test_synthetic_formula_exact():
    px = {"EURUSD": 1.08, "USDJPY": 150.0, "GBPUSD": 1.27, "USDCAD": 1.36, "USDSEK": 10.5, "USDCHF": 0.88}
    v, missing = dxy.synthetic_value(px)
    manual = 50.14348112 * 1.08 ** -0.576 * 150 ** 0.136 * 1.27 ** -0.119 * 1.36 ** 0.091 * 10.5 ** 0.042 * \
        0.88 ** 0.036
    assert v == pytest.approx(manual, rel=1e-12)
    assert missing == []
    assert 95 < v < 110  # realistic index level for these inputs


def test_synthetic_partial_and_mandatory():
    v, missing = dxy.synthetic_value({"EURUSD": 1.08, "USDJPY": 150})
    assert v is not None and set(missing) == {"GBPUSD", "USDCAD", "USDSEK", "USDCHF"}
    v, missing = dxy.synthetic_value({"EURUSD": 1.08})
    assert v is None and "USDJPY" in missing


def test_synthetic_bars_direction():
    eu = path_bars([1.10, 1.09, 1.08, 1.07], wick=0.001)
    uj = path_bars([150, 151, 152, 153], wick=0.1)
    bars, missing = dxy.synthetic_bars({"EURUSD": eu, "USDJPY": uj})
    assert len(bars) == 4 and "GBPUSD" in missing
    assert bars["close"].is_monotonic_increasing  # USD strengthening
    assert (bars["high"] >= bars["close"]).all() and (bars["low"] <= bars["close"]).all()


def test_correlation_negative_for_inverse():
    rng = np.random.default_rng(1)
    r = rng.normal(0, 0.001, 300)
    a = path_bars(list(100 * np.exp(np.cumsum(r))), step=900)
    b = path_bars(list(100 * np.exp(np.cumsum(-r + rng.normal(0, 0.0002, 300)))), step=900)
    c = dxy.returns_correlation(a, b, 192)
    assert c < -0.8


def test_dxy_modifier_gold_rules():
    cfg = {"max_modifier": 10, "decoupled_abs_corr": 0.3, "block_on_dxy_breakout": True}
    bull = {"bias": "bull", "below_vwap": False, "breaking_up": True}
    bear = {"bias": "bear", "below_vwap": True, "rejecting_resistance": True}
    gl = dxy.dxy_modifier("XAUUSD", "long", bear, -0.7, cfg)
    assert gl["points"] > 0 and not gl["block"]
    gb = dxy.dxy_modifier("XAUUSD", "long", bull, -0.7, cfg)
    assert gb["points"] < 0 and gb["block"]
    # direct pair: USDJPY long benefits from DXY bull
    uj = dxy.dxy_modifier("USDJPY", "long", bull, 0.8, cfg)
    assert uj["points"] > 0 and not uj["block"]
    # decoupled -> reduced weight, no block
    dc = dxy.dxy_modifier("XAUUSD", "long", bull, 0.1, cfg)
    assert dc["weight"] == pytest.approx(0.3) and not dc["block"]
    assert any("decoupled" in n for n in dc["notes"])
    # crosses unaffected
    assert dxy.dxy_modifier("EURJPY", "long", bull, None, cfg)["points"] == 0


def test_divergence_bonus():
    cfg = {"max_modifier": 10}
    st = {"bias": "neutral", "divergence": {"XAUUSD": "bullish"}}
    assert dxy.dxy_modifier("XAUUSD", "long", st, -0.6, cfg)["points"] > 0


def test_find_broker_dxy():
    assert dxy.find_broker_dxy(["EURUSD", "USDX.f", "DX.f"], ["DXY", "USDX", "DX.f"]) == "DX.f"
