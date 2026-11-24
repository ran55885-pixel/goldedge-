"""Execution (filling modes, retries, slippage, re-checks, deferral) and Trade Manager rules/time targets."""
import pytest

from helpers import WED_10UTC, World, bus_and_book, make_candidate, make_cfg, make_ctx, make_handle
from aurum_nexus.bots.execution import Execution, filling_sequence
from aurum_nexus.bots.risk import RiskManager
from aurum_nexus.bots.strategist import default_profile
from aurum_nexus.bots.trade_manager import TradeManager
from aurum_nexus.data.base import RET_INVALID_FILL, RET_REQUOTE, RET_TIMEOUT
from aurum_nexus.data.mock_adapter import MockAdapter
from aurum_nexus.indicators.structure import StructEvent


def plan_for(c, lots=0.1):
    return {"candidate_id": c.id, "account": "P1", "symbol": c.symbol, "direction": c.direction,
            "side": "buy" if c.direction == "long" else "sell", "entry": c.entry, "entry_type": "market",
            "stop": c.stop, "tp1": c.tp1, "tp2": c.tp2, "obstacle": c.obstacle, "lots": lots, "risk_money": 40.0,
            "risk_budget": 50, "risk_pct": 0.4, "rr": {}, "offset": 0.0, "size_factor": 1.0, "tp1_fraction": 0.5,
            "tp2_fraction": 0.3, "note": ""}


def exec_env(now=WED_10UTC + 20):
    cfg = make_cfg()
    st, bus, book = bus_and_book()
    w = World(now=now)
    rm = RiskManager(bus, cfg)
    tm = TradeManager(rm, cfg, storage=st)
    ex = Execution(bus, cfg, book, st, tm, sleep=lambda s: None, clock=w.clock)
    h = make_handle(cfg, w)
    return cfg, st, bus, book, w, ex, h, tm


def test_filling_sequence_detection():
    assert filling_sequence(1, ["IOC", "FOK", "RETURN"]) == [0, 2, 1]   # FOK only -> FOK first, IOC last resort
    assert filling_sequence(2, ["IOC", "FOK", "RETURN"]) == [1, 2, 0]   # IOC, RETURN, FOK last resort
    assert filling_sequence(0, ["IOC", "FOK", "RETURN"])[0] == 2         # neither flag -> RETURN


def test_open_paper_market_records_fill_and_time_targets():
    cfg, st, bus, book, w, ex, h, tm = exec_env()
    c = make_candidate()
    t = ex.open_market(plan_for(c), h, c, make_ctx("XAUUSD", w), default_profile("XAUUSD"), w.now)
    assert t is not None and t["entry"] == pytest.approx(2400.30) and t["mode"] == "paper"
    assert t["t1_deadline"] > w.now and t["t2_deadline"] >= t["t1_deadline"]
    fills = st.query("SELECT * FROM fills")
    assert len(fills) == 1 and fills[0]["slippage"] == pytest.approx(0.0)


def test_pre_send_rr_recheck_skips():
    cfg, st, bus, book, w, ex, h, tm = exec_env()
    c = make_candidate(stop=2396.30, tp1=2404.30, tp2=2404.30)
    w.prices["XAUUSD"] = (2401.0, 2401.30)   # price moved: RR to TP now 0.75
    assert ex.open_market(plan_for(c), h, c, make_ctx("XAUUSD", w), default_profile("XAUUSD"), w.now) is None
    assert any("at send" in e["message"] for e in bus.recent())


def test_retries_filling_switch_and_slippage_on_terminal():
    cfg, st, bus, book, w, ex, h, tm = exec_env()
    mock = MockAdapter(symbols=["EURUSD"], minutes=3000, seed=2)
    mock.allowed_fillings = {0}                # FOK only
    mock.script = [(RET_REQUOTE, 0.0), (RET_TIMEOUT, 0.0), (10009, 0.00002)]
    h.adapter = mock
    h.live = True
    tk = mock.tick("EURUSD")
    c = make_candidate("EURUSD", entry=tk.ask, stop=tk.ask - 0.0020, tp1=tk.ask + 0.0025, tp2=tk.ask + 0.0040)
    spec = mock.symbol_spec("EURUSD")
    spec.filling_mode = 2                      # broker claims IOC -> invalid fill -> switch
    mock.symbol_spec = lambda s: spec
    t = ex.open_market(plan_for(c), h, c, None, default_profile("EURUSD"), w.now)
    assert t is not None and t["mode"] == "live"
    fill = st.query("SELECT * FROM fills")[0]
    assert fill["attempts"] == 3 and fill["filling"] == "FOK"
    assert fill["slippage"] == pytest.approx(0.00002, abs=1e-9)
    ops = [s["filling"] for s in mock.sent]
    assert ops[0] == 1 and ops[-1] == 0     # IOC declared -> invalid fill -> ... -> FOK accepted


def test_post_fill_rr_check_closes_when_enabled():
    cfg, st, bus, book, w, ex, h, tm = exec_env()
    cfg.data["execution"]["postfill_rr_close"] = True
    mock = MockAdapter(symbols=["EURUSD"], minutes=3000, seed=2)
    tk = mock.tick("EURUSD")
    mock.script = [(10009, 0.0008), (10009, 0.0)]  # huge slippage on entry, then the close
    h.adapter, h.live = mock, True
    c = make_candidate("EURUSD", entry=tk.ask, stop=tk.ask - 0.0020, tp1=tk.ask + 0.0020, tp2=tk.ask + 0.0025)
    assert ex.open_market(plan_for(c), h, c, None, default_profile("EURUSD"), w.now) is None
    assert any("post-fill" in e["message"] for e in bus.recent())
    assert not mock.positions()


def test_deferral_first_seconds_and_rollover():
    cfg, st, bus, book, w, ex, h, tm = exec_env(now=WED_10UTC + 2)
    assert ex.must_defer(w.now) == "first seconds of a new candle"
    c = make_candidate()
    assert ex.submit(plan_for(c), h, c, make_ctx("XAUUSD", w), default_profile("XAUUSD"), w.now) is None
    assert len(ex.pending) == 1 and ex.pending[0]["kind"] == "deferred"
    w.now += 10
    opened = ex.process_pending({"XAUUSD": make_ctx("XAUUSD", w)}, w.now)
    assert len(opened) == 1 and not ex.pending
    roll = 1_700_085_600 + 80   # 2023-11-15 22:01:20 UTC = 17:01:20 New York (EST)
    assert ex.must_defer(roll) == "rollover / swap window"


# ------------------------------------------------------------------ Trade Manager
def open_trade(ex, h, w, **kw):
    c = make_candidate(**kw)
    return ex.open_market(plan_for(c, lots=0.2), h, c, make_ctx(c.symbol, w), default_profile(c.symbol), w.now)


def run_tm(tm, ex, t, h, w, ctx=None, dxy=None):
    tk = h.paper.tick(t["symbol"])
    acts = tm.evaluate(t, ctx or make_ctx(t["symbol"], w), tk.bid, tk.ask, w.now, dxy)
    for a in acts:
        if a["action"] == "close":
            ex.close(t, h, a["fraction"], a["reason"], a.get("exit_type", "early"), w.now, a.get("price"), a.get("rule"))
            if a.get("mark"):
                t[a["mark"]] = 1
        else:
            ex.modify_sl(t, h, a["sl"], a["reason"])
            if a.get("mark"):
                t[a["mark"]] = 1
    return acts


def test_tp1_partial_and_breakeven_then_tp2():
    cfg, st, bus, book, w, ex, h, tm = exec_env()
    t = open_trade(ex, h, w)
    w.prices["XAUUSD"] = (2404.40, 2404.70)
    acts = run_tm(tm, ex, t, h, w)
    assert acts[0]["exit_type"] == "tp1" and t["volume_open"] == pytest.approx(0.1)
    assert t["stop"] == pytest.approx(2400.30 + 0.30)   # breakeven + spread
    w.prices["XAUUSD"] = (2410.10, 2410.40)
    run_tm(tm, ex, t, h, w)
    assert t.get("tp2_done") and t["volume_open"] == pytest.approx(0.04)   # 0.3 of 0.2 closed, runner left


def test_stop_loss_paper_and_r_multiple():
    cfg, st, bus, book, w, ex, h, tm = exec_env()
    t = open_trade(ex, h, w)
    w.prices["XAUUSD"] = (2396.00, 2396.30)
    run_tm(tm, ex, t, h, w)
    assert t["status"] == "closed" and t["exit_type"] == "sl" and t["r_multiple"] == pytest.approx(-1.0)


def test_early_exit_ltf_choch_and_shadow():
    cfg, st, bus, book, w, ex, h, tm = exec_env()
    t = open_trade(ex, h, w)
    w.now += 600
    w.prices["XAUUSD"] = (2401.00, 2401.30)
    ctx = make_ctx("XAUUSD", w)
    ctx.tf["M5"].structure = {"events": [StructEvent(29, int(w.now - 60), 2401.5, "CHoCH", "bear", 20)], "swings": []}
    run_tm(tm, ex, t, h, w, ctx)
    assert t["status"] == "closed" and t["exit_type"] == "early"
    msgs = [e["message"] for e in bus.recent()]
    assert any(m.startswith("EARLY EXIT XAUUSD +0.17R: opposing CHoCH M5") for m in msgs), msgs
    assert len(tm.shadows) == 1
    w.prices["XAUUSD"] = (2410.5, 2410.8)
    tm.update_shadows({"XAUUSD": w.prices["XAUUSD"]}, w.now + 60)
    sh = st.shadows("closed")[0]
    assert sh["shadow_r"] > sh["exit_r"]     # holding would have reached the target


def test_time_targets_t1_tighten_then_t2_close():
    cfg, st, bus, book, w, ex, h, tm = exec_env()
    t = open_trade(ex, h, w)
    w.now = t["t1_deadline"] + 1
    w.prices["XAUUSD"] = (2400.50, 2400.80)
    acts = run_tm(tm, ex, t, h, w)
    assert acts and acts[0].get("rule") == "time_t1"
    assert t["stop"] == pytest.approx(2400.30 - 0.5 * 4.0)
    w.now = t["t2_deadline"] + 1
    acts = run_tm(tm, ex, t, h, w)
    assert t["status"] == "closed" and t["exit_type"] == "time"


def test_giveback_and_friday_close_and_dxy_shock():
    cfg, st, bus, book, w, ex, h, tm = exec_env()
    t = open_trade(ex, h, w)
    t["mfe_r"] = 1.5
    w.prices["XAUUSD"] = (2400.90, 2401.20)   # +0.15R
    run_tm(tm, ex, t, h, w)
    assert t["exit_reason"].startswith("giveback")
    t = open_trade(ex, h, w, symbol="EURUSD", entry=1.08508, stop=1.08300, tp1=1.08716, tp2=1.0900)
    w.now = WED_10UTC + 2 * 86400 + 11 * 3600   # Friday 21:00 UTC
    run_tm(tm, ex, t, h, w)
    assert t["exit_reason"] == "Friday close"
    w.now = WED_10UTC + 30
    w.prices["XAUUSD"] = (2400.00, 2400.30)
    t = open_trade(ex, h, w)
    dxy = {"shock": {"direction": "up", "time": int(w.now + 10), "atr_mult": 2.5}}
    w.now += 60
    run_tm(tm, ex, t, h, w, dxy=dxy)
    assert "DXY shock" in t["exit_reason"]


def test_eta_text():
    cfg, st, bus, book, w, ex, h, tm = exec_env()
    t = open_trade(ex, h, w)
    w.now += 420
    e = tm.eta(t, make_ctx("XAUUSD", w), 2401.0, w.now)
    assert e["text"].startswith("TP1 ETA") and "elapsed 7m" in e["text"]


def test_small_slippage_keeps_single_target_trade():
    cfg, st, bus, book, w, ex, h, tm = exec_env()
    mock = MockAdapter(symbols=["EURUSD"], minutes=3000, seed=2)
    tk = mock.tick("EURUSD")
    mock.script = [(10009, 0.00002)]          # 0.2 pip slippage on a 20-pip stop / exactly-1R single target
    h.adapter, h.live = mock, True
    c = make_candidate("EURUSD", entry=tk.ask, stop=tk.ask - 0.0020, tp1=tk.ask + 0.0020, tp2=tk.ask + 0.0020)
    t = ex.open_market(plan_for(c), h, c, None, default_profile("EURUSD"), w.now)
    assert t is not None and t["tp2"] >= t["tp1"] - 1e-12
    assert not any("post-fill" in e["message"] for e in bus.recent())


def test_post_fill_default_keeps_trade():
    cfg, st, bus, book, w, ex, h, tm = exec_env()
    mock = MockAdapter(symbols=["EURUSD"], minutes=3000, seed=2)
    tk = mock.tick("EURUSD")
    mock.script = [(10009, 0.0008)]          # big slippage: trade is still kept, stop unchanged
    h.adapter, h.live = mock, True
    c = make_candidate("EURUSD", entry=tk.ask, stop=tk.ask - 0.0020, tp1=tk.ask + 0.0020, tp2=tk.ask + 0.0025)
    t = ex.open_market(plan_for(c), h, c, None, default_profile("EURUSD"), w.now)
    assert t is not None and t["stop"] == c.stop and mock.positions()
