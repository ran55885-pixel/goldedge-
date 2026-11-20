"""Levels clustering/grades/state machine (incl. STALLED, BROKEN role flip) and Signal scoring rules."""
import numpy as np
import pandas as pd
import pytest

from conftest import make_bars
from helpers import WED_10UTC, World, make_cfg, make_ctx
from aurum_nexus.bots.levels import LevelsAgent, grade_for
from aurum_nexus.bots.signal import SignalBot
from aurum_nexus.bots.strategist import Strategist
from aurum_nexus.context import TFData
from aurum_nexus.logbus import LogBus
from aurum_nexus.models import Level
from aurum_nexus.storage import Storage


class FeedStub:
    def __init__(self, now):
        self._now = now

    def now(self):
        return self._now

    def source_for(self, s):
        return None


def agent(now=WED_10UTC, data=None):
    cfg = make_cfg(data)
    return LevelsAgent(LogBus(), cfg, FeedStub(now), clock=lambda: now), cfg


def m5_tf(rows, start):
    b = make_bars(rows, start=start, step=300)
    return TFData(tf="M5", bars=b, atr=1.0, structure={"events": [], "swings": []})


# ------------------------------------------------------------------ clustering & scoring
def test_grades():
    th = {"A+": 80, "A": 65, "B": 50}
    assert [grade_for(x, th) for x in (85, 70, 55, 10)] == ["A+", "A", "B", "C"]


def test_cluster_confluence_scores_higher():
    ag, cfg = agent()
    w = World()
    ctx = make_ctx("XAUUSD", w, atr_m5=1.0)
    ctx.tf["H1"].atr = 4.0
    ctx.adr = 30.0
    ctx.daily = {"pdh": 2410.0, "pdl": 2380.0}
    ctx.vp["composite"] = {"poc": 2410.4, "vah": 2420.0, "val": 2370.0, "hvn": [], "lvn": []}
    ctx.tf["H1"].zones = [Level("supply", 2409.5, 2411.0, "H1", "resistance", 0.8)]
    lv = ag.build_map(ctx)
    top = max(lv, key=lambda x: x["score"])
    assert 2409 <= top["mid"] <= 2411.5 and top["n_sources"] >= 3
    lone = [x for x in lv if abs(x["mid"] - 2380.0) < 0.5][0]
    assert top["score"] > lone["score"]
    assert top["role"] == "resistance" and lone["role"] == "support"


# ------------------------------------------------------------------ state machine
def lv_dict(lo, hi):
    return {"id": "X:1", "low": lo, "high": hi, "mid": (lo + hi) / 2, "grade": "A", "role": "support", "score": 70}


def test_waiting_watching_ready():
    ag, cfg = agent()
    w = World(prices={"XAUUSD": (2404.0, 2404.2)})
    ctx = make_ctx("XAUUSD", w, atr_m5=1.0)
    ctx.has_volume = False
    st = {"state": "WAITING", "since": 0, "entered": None, "role": "support", "reactions": [], "flipped": False,
          "ready_until": 0, "last_bar": 0}
    lv = lv_dict(2400.0, 2401.0)
    base = WED_10UTC - 300 * 10
    rows = [(2405, 2405.5, 2404.5, 2405)] * 6
    ag._advance(ctx, lv, st, m5_tf(rows, base), 1.0, watch=5.0, price=2404.1, now=w.now)
    assert st["state"] == "WATCHING"
    # price enters the zone, next bar prints a long rejection wick from inside the zone
    rows2 = rows + [(2402, 2402.2, 2400.6, 2401.2), (2401.0, 2401.5, 2400.1, 2401.3)]
    ag._advance(ctx, lv, st, m5_tf(rows2[:-1], base), 1.0, 5.0, 2400.8, w.now)
    assert st["entered"] is not None
    ag._advance(ctx, lv, st, m5_tf(rows2, base), 1.0, 5.0, 2402.4, w.now + 300)
    assert st["state"] == "READY" and "rejection wick" in st["reactions"], st["reactions"]


def test_stalled_when_no_reaction_in_time():
    ag, cfg = agent()
    w = World()
    ctx = make_ctx("XAUUSD", w, atr_m5=1.0)
    ctx.has_volume = False
    st = {"state": "WATCHING", "since": 0, "entered": None, "role": "support", "reactions": [], "flipped": False,
          "ready_until": 0, "last_bar": 0}
    lv = lv_dict(2400.0, 2401.0)
    base = WED_10UTC - 300 * 10
    rows = [(2402, 2402.2, 2401.8, 2402)] * 5
    drift = [(2401.0, 2401.1, 2400.4, 2400.5), (2400.5, 2400.7, 2400.3, 2400.4), (2400.4, 2400.6, 2400.2, 2400.3),
             (2400.3, 2400.5, 2400.15, 2400.2)]
    for k in range(1, len(drift) + 1):
        ag._advance(ctx, lv, st, m5_tf(rows + drift[:k], base), 1.0, 5.0, drift[k - 1][3], w.now + 300 * k)
        if k < 3:
            assert st["state"] == "WATCHING" and st["countdown"]["bars_left"] == 3 - k
    assert st["state"] == "STALLED"


def test_broken_role_flip():
    ag, cfg = agent()
    w = World()
    ctx = make_ctx("XAUUSD", w, atr_m5=1.0)
    ctx.has_volume = False
    st = {"state": "WATCHING", "since": 0, "entered": None, "role": "support", "reactions": [], "flipped": False,
          "ready_until": 0, "last_bar": 0}
    lv = lv_dict(2400.0, 2401.0)
    rows = [(2402, 2402.2, 2401.8, 2402)] * 5 + [(2401, 2401, 2398.5, 2399.0), (2399, 2399.3, 2397.9, 2398.0)]
    ag._advance(ctx, lv, st, m5_tf(rows, WED_10UTC - 3000), 1.0, 5.0, 2398.0, w.now)
    assert st["state"] == "BROKEN" and st["role"] == "resistance" and st["flipped"]


# ------------------------------------------------------------------ signal scoring
def signal_env(levels_map=None, adr_used=None):
    cfg = make_cfg()
    st = Storage(":memory:")
    strat = Strategist(LogBus(st), cfg, st, profiles_dir=None)
    strat.dir = __import__("pathlib").Path("/nonexistent")
    ag, _ = agent()
    ag.maps["XAUUSD"] = levels_map or []
    sig = SignalBot(LogBus(st), cfg, ag, strat)
    w = World()
    ctx = make_ctx("XAUUSD", w, atr_m5=2.0)
    ctx.tf["M5"].last_bar_time = WED_10UTC - 300
    ctx.adr_used = adr_used
    ctx.daily = {"daily_open": 2390.0}
    return sig, ctx, strat


def test_score_weights_sum_to_100(cfg):
    assert sum(cfg.get("signal.weights").values()) == 100


def test_skip_when_obstacle_closer_than_1r():
    obstacle = {"id": "o", "low": 2402.8, "high": 2404.0, "mid": 2403.4, "grade": "A", "role": "resistance",
                "score": 70, "n_sources": 2, "state": "WAITING"}
    sig, ctx, strat = signal_env([obstacle])
    raw = {"setup": "sweep_reversal", "tf": "M5", "direction": "long", "anchor": 2397.80, "zone": (2397.8, 2399),
           "zone_id": "z", "zone_quality": 0.7, "notes": [], "edge": 2399.0}
    c = sig.finalize(ctx, raw, strat.profile("XAUUSD"), None)
    assert c is None
    reasons = [s["reason"] for s in sig.skips["XAUUSD"]]
    import re
    m = re.match(r"RR (0\.\d+) < 1\.0 \(next obstacle", reasons[-1])
    assert m and float(m.group(1)) < 1.0, reasons[-1]


def test_adr_exhaustion_blocks_direction_of_move():
    sig, ctx, strat = signal_env(adr_used=91.0)
    raw = {"setup": "sweep_reversal", "tf": "M5", "direction": "long", "anchor": 2397.0, "zone": (2397, 2399),
           "zone_id": "z", "zone_quality": 0.7, "notes": [], "edge": 2399.0}
    assert sig.finalize(ctx, raw, strat.profile("XAUUSD"), None) is None
    assert "ADR 91% used" in sig.skips["XAUUSD"][-1]["reason"]
    raw["direction"], raw["anchor"], raw["edge"] = "short", 2403.0, 2401.0
    assert sig.finalize(ctx, raw, strat.profile("XAUUSD"), None) is not None   # counter-move allowed


def test_candidate_scores_and_rr_floor():
    sig, ctx, strat = signal_env()
    raw = {"setup": "sweep_reversal", "tf": "M5", "direction": "long", "anchor": 2397.0, "zone": (2397, 2399),
           "zone_id": "z", "zone_quality": 0.8, "notes": ["test"], "edge": 2399.0}
    c = sig.finalize(ctx, raw, strat.profile("XAUUSD"), None)
    assert c is not None and 0 <= c.score <= 100
    assert set(c.components) == set(sig.sc["weights"])
    assert c.rr_tp1 >= 1.0 and c.rr_final >= 1.0
    assert c.entry == pytest.approx(2400.30) and c.stop < 2397.0       # structure anchor + ATR buffer
    assert c.components["volume_flow"] == pytest.approx(0.4) or ctx.has_volume


def test_dxy_block_flag_and_modifier():
    sig, ctx, strat = signal_env()
    raw = {"setup": "sweep_reversal", "tf": "M5", "direction": "long", "anchor": 2397.0, "zone": (2397, 2399),
           "zone_id": "z", "zone_quality": 0.8, "notes": [], "edge": 2399.0}
    dxy = {"available": True, "bias": "bull", "breaking_up": True, "below_vwap": False,
           "correlations": {"XAUUSD": -0.7}}
    c = sig.finalize(ctx, raw, strat.profile("XAUUSD"), dxy)
    assert c.modifiers["dxy"] < 0 and c.modifiers.get("dxy_block") == 1


def test_dedup_across_timeframes():
    from aurum_nexus.models import Candidate
    sig, ctx, strat = signal_env()
    a = Candidate("XAUUSD", "long", "zone_reaction", "M1", 1, "market", 0, score=80, zone_id="Z")
    b = Candidate("XAUUSD", "long", "zone_reaction", "M5", 1, "market", 0, score=85, zone_id="Z")
    best = {}
    for c in (a, b):
        k = (c.direction, c.zone_id)
        if k not in best or c.score > best[k].score:
            best[k] = c
    assert list(best.values()) == [b]
