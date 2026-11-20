import math

import numpy as np
import pandas as pd
import pytest

from conftest import T0, make_bars, path_bars
from aurum_nexus.indicators import atr as atrm
from aurum_nexus.indicators import candles, fvg_ob, levels_basic, liquidity, orderflow, regime, structure
from aurum_nexus.indicators import volume_profile as vp
from aurum_nexus.indicators import vwap as vwm
from aurum_nexus.indicators.zones import find_zones


# ------------------------------------------------------------------ ATR / ADR
def test_atr_constant_range():
    b = make_bars([(10, 11, 9, 10)] * 30)
    a = atrm.atr(b.high.values, b.low.values, b.close.values, 14)
    assert a[-1] == pytest.approx(2.0)


def test_adr_and_used_pct():
    d = make_bars([(100, 110, 100, 105), (105, 125, 105, 120), (120, 130, 100, 110), (110, 115, 105, 112)],
                  step=86400)
    # completed days: ranges 10, 20, 30 -> 20
    assert atrm.adr(d, 14) == pytest.approx(20.0)
    assert atrm.adr_used_pct(115, 105, 20.0) == pytest.approx(50.0)


def test_atr_percentile_regime():
    s = np.concatenate([np.ones(80), np.linspace(1, 3, 21)])
    assert atrm.atr_percentile(s) > 80
    assert atrm.atr_regime(90) == "expansion"
    assert atrm.atr_regime(10) == "compression"


# ------------------------------------------------------------------ structure
def zigzag():
    # up-trend: low 100 -> 110 -> 104 -> 116 -> 108 -> 122 then breakdown below 108
    pts = [100, 103, 106, 110, 108, 106, 104, 107, 111, 116, 113, 110, 108, 112, 117, 122, 118, 114, 110, 106, 103,
           101, 100, 99]
    return path_bars(pts, wick=0.1)


def test_swings_labels_and_bos_choch():
    st = structure.analyze(zigzag(), k=2)
    kinds = [(s.kind, s.label) for s in st["swings"]]
    assert ("H", "HH") in kinds and ("L", "HL") in kinds
    ev = [(e.kind, e.direction) for e in st["events"]]
    assert ("BOS", "bull") in ev
    assert ev[-1] == ("CHoCH", "bear")
    assert st["trend"] == "bear"
    assert st["dealing_range"]["high"] >= 122


def test_premium_discount():
    st = structure.analyze(zigzag(), k=2)
    assert st["zone"] == "discount"  # price fell to the bottom of the range
    assert 0 <= st["position"] < 0.5


# ------------------------------------------------------------------ zones
def zone_bars(close_through=False):
    rows = [(100, 100.5, 99.5, 100)] * 20                  # quiet history (ATR ~1)
    rows += [(100, 100.4, 99.8, 100.1), (100.1, 100.3, 99.9, 100.0)]  # base (2 small candles)
    rows += [(100.0, 103.5, 99.9, 103.4)]                  # bullish impulse (range 3.6 ATR)
    rows += [(103.4, 104, 103, 103.8)] * 5
    if close_through:
        rows += [(103.8, 103.8, 98.5, 98.9)]               # close below the zone -> invalid
    return make_bars(rows)


def test_demand_zone_detected():
    zs = find_zones(zone_bars(), "M5")
    dem = [z for z in zs if z.kind == "demand"]
    assert dem, zs
    z = dem[0]
    assert z.low == pytest.approx(99.8)
    assert z.high == pytest.approx(100.1)
    assert z.fresh and z.touches == 0
    assert z.meta["departure_atr"] > 1.5


def test_zone_expires_on_close_through():
    zs = find_zones(zone_bars(close_through=True), "M5")
    assert not [z for z in zs if z.kind == "demand" and z.low == pytest.approx(99.8)]


def test_zone_expires_after_two_touches():
    rows = zone_bars().values.tolist()
    b = zone_bars()
    extra = []
    for _ in range(3):  # three separate touches
        extra += [(103.0, 103.2, 100.05, 101.0), (101, 104, 101, 103.9)]
    b2 = pd.concat([b, make_bars(extra, start=int(b.time.iloc[-1]) + 60)], ignore_index=True)
    zs = find_zones(b2, "M5")
    assert not [z for z in zs if z.kind == "demand" and z.low == pytest.approx(99.8)]
    assert rows  # sanity


# ------------------------------------------------------------------ basic levels
def test_round_levels_scale_with_adr():
    gold = levels_basic.round_steps(3590.0, 60.0, {"adr_fractions": [0.25, 0.5, 1.0]})
    fx = levels_basic.round_steps(1.0966, 0.0070, {"adr_fractions": [0.25, 0.5, 1.0]})
    assert gold == [20.0, 25.0, 50.0] or gold[0] >= 10
    assert all(s < 0.02 for s in fx) and fx[0] >= 0.001
    assert levels_basic.nice_step(0.0035) == pytest.approx(0.005)


def test_daily_weekly_levels():
    # 3 days of hourly bars, day boundary 17:00 NY
    rows = []
    for d in range(3):
        for h in range(24):
            base = 100 + d * 10
            rows.append((base, base + 2 + d, base - 1 - d, base + 1))
    b = make_bars(rows, step=3600)
    out = levels_basic.daily_weekly(b)
    assert "pdh" in out and "pdl" in out and out["pdh"] > out["pdl"]
    assert out["today_high"] >= out["daily_open"]


def test_pivot_clusters():
    sw = [structure.Swing(1, T0, 100.0, "H"), structure.Swing(5, T0, 100.2, "H"), structure.Swing(9, T0, 90.0, "L")]
    lv = levels_basic.pivot_clusters(sw, 0.5, "H1")
    assert len(lv) == 1 and lv[0].touches == 2 and lv[0].side == "resistance"


# ------------------------------------------------------------------ liquidity / FVG / OB
def test_equal_highs_and_sweep():
    pts = [100, 102, 105, 103, 101, 103, 105.02, 103, 101, 102, 104, 106, 104.5]
    b = path_bars(pts, wick=0.0)
    st = structure.analyze(b, k=2)
    pools = liquidity.equal_levels(st["swings"], b, tol=0.1, timeframe="M5")
    eqh = [p for p in pools if p.kind == "eqh"]
    assert eqh and eqh[0].meta["swept"] is True
    sw = liquidity.detect_sweep(make_bars([(104, 106, 103.5, 104.5)]), 105.0, "high", 1)
    assert sw and sw["direction"] == "short"


def test_fvg_detect_and_fill():
    rows = [(100, 100.5, 99.5, 100)] * 20 + [(100, 100.5, 99.8, 100.4), (100.4, 103, 100.4, 102.9),
                                              (102.9, 103.5, 101.5, 103.2)]
    b = make_bars(rows)
    f = fvg_ob.find_fvgs(b, "M5")
    assert f and f[-1].kind == "fvg_bull" and f[-1].low == pytest.approx(100.5) and f[-1].high == pytest.approx(101.5)
    filled = pd.concat([b, make_bars([(103, 103, 100.0, 100.2)], start=int(b.time.iloc[-1]) + 60)], ignore_index=True)
    assert not [x for x in fvg_ob.find_fvgs(filled, "M5") if x.low == pytest.approx(100.5)]


def test_order_block_from_bos():
    b = zigzag()
    st = structure.analyze(b, k=2)
    obs = fvg_ob.find_order_blocks(b, st["events"], "M5")
    assert all(o.kind in ("ob_bull", "ob_bear") for o in obs)


# ------------------------------------------------------------------ volume profile / VWAP
def test_volume_profile_poc_and_value_area():
    rows = [(100, 101, 99, 100, 10)] * 10 + [(105, 105.2, 104.8, 105, 500)] * 10 + [(110, 111, 109, 110, 10)] * 10
    p = vp.profile(make_bars(rows), rows=60)
    assert abs(p["poc"] - 105) <= 0.2 + 0.11  # within one bin (bin width 0.2)
    assert p["val"] <= 105 <= p["vah"]
    tot = sum(p["volumes"])
    inside = sum(v for c, v in zip(p["bins"], p["volumes"]) if p["val"] <= c <= p["vah"])
    assert inside / tot >= 0.69


def test_vwap_bands():
    b = make_bars([(10, 10, 10, 10, 1), (20, 20, 20, 20, 1)])
    v = vwm.anchored(b, T0)
    assert v["last"] == pytest.approx(15.0)
    assert v["last_sd"] == pytest.approx(5.0)
    assert v["u2"][-1] == pytest.approx(25.0) and v["l1"][-1] == pytest.approx(10.0)
    assert vwm.band_position(25.0, v) == pytest.approx(2.0)


def test_twap_when_no_volume():
    b = make_bars([(10, 10, 10, 10, 0), (20, 20, 20, 20, 0)])
    v = vwm.anchored(b, T0)
    assert v["basis"].startswith("TWAP")


# ------------------------------------------------------------------ order flow
def test_delta_clv_and_cvd_reset():
    b = make_bars([(0, 10, 0, 10, 100), (10, 10, 0, 0, 50), (5, 10, 0, 5, 80)])
    d = orderflow.delta_clv(b)
    assert list(d) == [100.0, -50.0, 0.0]
    c = orderflow.cvd(d, np.array([1, 1, 2]))
    assert list(c) == [100.0, 50.0, 0.0]


def test_delta_from_m1():
    m1 = make_bars([(1, 2, 0.5, 1.5, 10), (1.5, 2, 1, 1.2, 4), (1.2, 2, 1, 1.9, 6)], step=60)
    m5 = make_bars([(1, 2, 0.5, 1.9, 20)], step=300)
    d = orderflow.delta_from_m1(m5, m1, 300)
    assert d[0] == pytest.approx(10 - 4 + 6)


def test_rvol_same_time_of_day():
    rows = []
    for day in range(6):
        for slot in range(3):
            v = 100 if day < 5 else (300 if slot == 1 else 100)
            rows.append((1, 1, 1, 1, v))
    # 3 bars per day at the same times
    out = []
    for i, r in enumerate(rows):
        day, slot = divmod(i, 3)
        out.append({"time": T0 + day * 86400 + slot * 300, "open": 1, "high": 1, "low": 1, "close": 1,
                    "tick_volume": r[4], "real_volume": 0.0, "spread": 0})
    rv = orderflow.rvol(pd.DataFrame(out), days=10)
    assert rv[-2] == pytest.approx(3.0)
    assert rv[-1] == pytest.approx(1.0)


def test_cvd_divergence():
    sw = [structure.Swing(2, T0, 105, "H"), structure.Swing(8, T0 + 360, 107, "H")]
    c = np.zeros(10)
    c[2], c[8] = 500, 300
    b = make_bars([(1, 1, 1, 1)] * 10)
    d = orderflow.divergences(b, sw, c)
    assert d and d[0]["type"] == "bearish"


def test_absorption_and_spike_flags():
    rows = [(100, 101, 99, 100, 100)] * 30 + [(100, 100.3, 99.9, 100.1, 400)]
    f = orderflow.flags(make_bars(rows), rv=np.array([1.0] * 30 + [4.0]))
    assert f["spike"][-1] and f["absorption"][-1] and not f["climax"][-1]


# ------------------------------------------------------------------ candles / regime
def test_candles():
    b = make_bars([(10, 10.5, 8.9, 9), (8.8, 10.8, 8.7, 10.6)])
    assert candles.engulfing(b) == "bull"
    pin = make_bars([(10, 10.2, 8, 10.1)])
    assert candles.rejection(pin) == "bull"


def test_regime():
    r = regime.classify("bull", 3, 0, 50, 60, False)
    assert r["regime"] == "trend"
    r = regime.classify("neutral", 0, 3, 50, 50, True)
    assert r["regime"] == "range"
    assert regime.classify("neutral", 0, 0, 90, 95, None)["regime"] == "expansion"
    assert regime.classify("neutral", 0, 0, 5, 20, None)["regime"] == "compression"
