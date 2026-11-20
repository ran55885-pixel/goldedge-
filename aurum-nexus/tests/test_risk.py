import pytest

from helpers import World, make_candidate, make_cfg
from aurum_nexus.bots.risk import RiskManager, loss_at_stop, lot_size, rr_check
from aurum_nexus.config import config_from_dict
from aurum_nexus.data.specs import generic_spec
from aurum_nexus.logbus import LogBus
from aurum_nexus.models import SymbolSpec


def gold_spec(**kw):
    d = dict(name="XAUUSD", broker_name="XAUUSD", digits=2, point=0.01, tick_size=0.01, tick_value=1.0,
             contract_size=100, volume_min=0.01, volume_max=50.0, volume_step=0.01)
    d.update(kw)
    return SymbolSpec(**d)


# ------------------------------------------------------------------ lot sizing
def test_gold_lot_size():
    # $100 risk, SL 4.00 away -> 400 ticks x $1 = $400/lot -> 0.25 lots
    lots, _ = lot_size(100.0, 2400.0, 2396.0, gold_spec())
    assert lots == pytest.approx(0.25)
    assert loss_at_stop(lots, 2400.0, 2396.0, gold_spec()) <= 100.0 + 1e-9


def test_eurusd_lot_size():
    spec = generic_spec("EURUSD")
    assert spec.tick_size == pytest.approx(0.00001) and spec.tick_value == pytest.approx(1.0)
    # $50 risk, 25 pip SL (0.00250) -> 250 ticks x $1 = $250 per lot -> 0.20 lots
    lots, _ = lot_size(50.0, 1.08500, 1.08250, spec)
    assert lots == pytest.approx(0.20)


def test_usdjpy_lot_size_converted_to_account_ccy():
    spec = generic_spec("USDJPY", "USD", lambda f, t: 1 / 150.0 if (f, t) == ("JPY", "USD") else None)
    # tick value: 0.001 * 100000 JPY = 100 JPY = 0.6667 USD
    assert spec.tick_value == pytest.approx(100 / 150.0)
    lots, _ = lot_size(100.0, 150.000, 149.700, spec)   # 300 ticks * 0.6667 = 200 USD/lot -> 0.5
    assert lots == pytest.approx(0.50)


def test_never_round_up_and_volume_rules():
    sp = gold_spec(volume_step=0.1, volume_min=0.1)
    lots, _ = lot_size(100.0, 2400.0, 2396.0, sp)   # raw 0.25 -> floor to 0.2 (never 0.3)
    assert lots == pytest.approx(0.2)
    lots, note = lot_size(30.0, 2400.0, 2396.0, sp)  # raw 0.075 < min 0.1 -> skip, never round up
    assert lots == 0 and "never round up" in note
    lots, _ = lot_size(1e9, 2400.0, 2396.0, gold_spec(volume_max=5.0))
    assert lots == 5.0


def test_lot_floor_float_precision():
    lots, _ = lot_size(0.3 * 400.0, 2400.0, 2396.0, gold_spec())  # exactly 0.30
    assert lots == pytest.approx(0.30)


# ------------------------------------------------------------------ RR floor incl. spread
def test_rr_check_floor_and_obstacle():
    ok, rr, why = rr_check("long", 100.0, 99.0, 101.0, 102.0, 101.5, 1.0)
    assert ok and rr["obstacle"] == pytest.approx(1.5)
    ok, rr, why = rr_check("long", 100.0, 99.0, 101.0, 102.0, 100.8, 1.0)
    assert not ok and why.startswith("RR 0.80 < 1.0")


def test_rr_includes_spread_via_realistic_entry():
    """Plan built on mid looks like 1:1, but the realistic ask entry makes it < 1 -> skipped."""
    cfg = make_cfg()
    rm = RiskManager(LogBus(), cfg)
    c = make_candidate(entry=2400.00, stop=2396.00, tp1=2404.00, tp2=2404.00)
    plan, why = rm.plan(c, "P1", 0.5, 10000, gold_spec(), bid=2400.0, ask=2400.40, offset=0.0, factor=1.0,
                        profile={"targets": {"tp1_fraction": 0.5, "tp2_fraction": 0.3}, "risk_mult": 1.0})
    assert plan is None and why.startswith("SKIPPED RR")


def test_plan_rebases_to_account_price_and_sizes():
    cfg = make_cfg()
    rm = RiskManager(LogBus(), cfg)
    c = make_candidate(entry=2400.30, stop=2396.30, tp1=2404.30, tp2=2410.0)
    plan, why = rm.plan(c, "P1", 0.5, 10000, gold_spec(), bid=2401.0, ask=2401.30, offset=1.0, factor=1.0,
                        profile={"targets": {"tp1_fraction": 0.5, "tp2_fraction": 0.3}, "risk_mult": 1.0})
    assert why == "ok"
    assert plan["stop"] == pytest.approx(2397.30) and plan["tp2"] == pytest.approx(2411.0)
    assert plan["entry"] == pytest.approx(2401.30)
    assert plan["risk_money"] <= 50.0 + 1e-6 and plan["lots"] == pytest.approx(0.12)


# ------------------------------------------------------------------ floors in config
def test_config_floors_cannot_be_relaxed():
    cfg = config_from_dict({"risk": {"min_rr": 0.8, "min_score": 55, "tp1_rr": 0.5},
                            "guardian": {"refuse_live_on_fallback": False}})
    assert cfg.min_rr == 1.0 and cfg.min_score == 70
    assert cfg.get("risk.tp1_rr") == 1.0
    assert cfg.get("guardian.refuse_live_on_fallback") is True
    assert any("min_rr" in w for w in cfg.warnings) and any("min_score" in w for w in cfg.warnings)
    up = config_from_dict({"risk": {"min_rr": 1.5, "min_score": 80}})
    assert up.min_rr == 1.5 and up.min_score == 80
    assert not [w for w in up.warnings if "min_rr=" in w or "min_score=" in w]
    assert up.get("risk.tp1_rr") == 1.5   # TP1 follows a raised floor


def test_plain_password_in_yaml_is_ignored():
    cfg = config_from_dict({}, [{"name": "A", "password": "secret!", "password_env": "X"}])
    assert any("plain 'password'" in w for w in cfg.warnings)
    assert "secret!" not in str(cfg.accounts[0].public_dict())


# ------------------------------------------------------------------ size reduction & profit lock
def test_size_factor_multiplicative():
    rm = RiskManager(LogBus(), make_cfg())
    closed = [{"r_multiple": -1}, {"r_multiple": -1}] + [{"r_multiple": -0.5}] * 8
    f, notes = rm.size_factor(closed, equity=9000, peak_equity=10000)   # 10% DD
    assert f == pytest.approx(0.25)  # 0.5 * 0.5 * 0.75 = 0.1875 -> floored at min_size_factor 0.25
    f2, _ = rm.size_factor([{"r_multiple": 1}], 10000, 10000)
    assert f2 == 1.0


def test_daily_profit_lock():
    rm = RiskManager(LogBus(), make_cfg())
    assert rm.profit_lock("P1", [1.0, 1.0, 1.2])[0] is True
    locked, why = rm.profit_lock("P1", [2.0, -1.2])     # peak 2.0, now 0.8 -> gave back 60%
    assert locked and "gave back" in why
    assert rm.profit_lock("P1", [1.0, -0.2])[0] is False


def test_tp1_rebased_after_slippage_keeps_floor():
    from aurum_nexus.bots.risk import rebase_tp1
    # planned entry 100, stop 99, TP1 101 (1R). Filled at 100.02 -> TP1 must move to 101.0402 (1R from fill)
    assert rebase_tp1("long", 100.02, 99.0, 101.0, 1.0, 1.0) == pytest.approx(101.04)
    assert rebase_tp1("short", 99.98, 101.0, 99.0, 1.0, 1.0) == pytest.approx(98.96)
    assert rebase_tp1("long", 99.9, 99.0, 101.0, 1.0, 1.0) == 101.0      # better fill: TP1 never pulled closer
    ok, rr, _ = rr_check("long", 100.02, 99.0, rebase_tp1("long", 100.02, 99.0, 101.0, 1.0, 1.0), 102.5, None, 1.0)
    assert ok and rr["tp1"] >= 1.0 - 1e-9
