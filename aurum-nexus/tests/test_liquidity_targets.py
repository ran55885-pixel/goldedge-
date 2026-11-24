"""Liquidity -> liquidity targets: nearest opposing pool beyond min RR, front-run, capped at liq_max_rr."""
from aurum_nexus.bots.signal import SignalBot


def _bot(pools):
    b = object.__new__(SignalBot)
    b._liquidity_targets = lambda ctx, tf: pools
    return b


def test_long_goals_sorted_filtered():
    pools = [("high", 101.0, "M15 swing high", 0.5), ("high", 103.0, "PDH", 0.8), ("high", 120.0, "PWH", 0.9),
             ("low", 95.0, "PDL", 0.8), ("high", 99.0, "below entry", 0.5)]
    g = _bot(pools).liquidity_goals(None, "M5", "long", 100.0, 1.0, 1.0, 1.0, {"liq_front_atr": 0.1, "liq_max_rr": 6})
    # 101 -> 100.9 = 0.9R (< min 1R, dropped); 103 -> 102.9 kept; 120 beyond 6R dropped; lows ignored
    assert [lab for _, lab in g] == ["PDH"] and abs(g[0][0] - 102.9) < 1e-9


def test_short_goals():
    pools = [("low", 97.0, "Asia low", 0.7), ("low", 95.0, "PDL", 0.8), ("high", 105.0, "PDH", 0.8)]
    g = _bot(pools).liquidity_goals(None, "M5", "short", 100.0, 1.0, 1.0, 1.0, {"liq_front_atr": 0.0})
    assert [lab for _, lab in g] == ["Asia low", "PDL"]
