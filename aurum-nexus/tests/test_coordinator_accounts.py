"""Coordinator ranking (multi-entry in score order, ceiling, risk cap, correlation) and account manager."""
import pytest

from helpers import WED_10UTC, World, make_candidate, make_cfg, make_ctx, make_handle
from aurum_nexus.bots.accounts import AccountHandle, AccountsManager
from aurum_nexus.bots.execution import Execution
from aurum_nexus.bots.guardian import Guardian
from aurum_nexus.bots.risk import RiskManager
from aurum_nexus.bots.strategist import Strategist
from aurum_nexus.bots.trade_manager import TradeManager
from aurum_nexus.coordinator import Coordinator
from aurum_nexus.data.mock_adapter import MockAdapter
from aurum_nexus.hub.worker import WorkerProxy
from aurum_nexus.logbus import LogBus
from aurum_nexus.storage import Storage
from aurum_nexus.tradebook import TradeBook


def coord_env(data=None):
    cfg = make_cfg(data)
    st = Storage(":memory:")
    bus = LogBus(st)
    book = TradeBook(st)
    w = World(now=WED_10UTC + 20)
    rm = RiskManager(bus, cfg)
    g = Guardian(bus, cfg, book, None, rm, clock=w.clock)
    tm = TradeManager(rm, cfg, storage=st)
    ex = Execution(bus, cfg, book, st, tm, sleep=lambda s: None, clock=w.clock)
    am = AccountsManager(bus, cfg, st, clock=w.clock)
    h = make_handle(cfg, w)
    am.add(h)
    strat = Strategist(bus, cfg, st)
    strat.dir = __import__("pathlib").Path("/nonexistent")
    ctxs = {s: make_ctx(s, w) for s in w.prices}
    co = Coordinator(cfg, bus, st, am, rm, g, ex, strat, None, lambda: ctxs)
    return co, book, w, bus


def cands():
    return [
        make_candidate("EURUSD", entry=1.08508, stop=1.08408, tp1=1.08608, tp2=1.08708, score=78),
        make_candidate("XAUUSD", score=91),
        make_candidate("USDJPY", entry=150.012, stop=149.900, tp1=150.124, tp2=150.3, score=84),
        make_candidate("GBPUSD", entry=1.2701, stop=1.2690, tp1=1.2712, tp2=1.2730, score=62),
    ]


def test_multi_entry_in_score_order_and_below_min():
    co, book, w, bus = coord_env()
    res = co.select(cands(), w.now)
    order = [r["id"].split("-")[0] for r in res]
    assert order == ["XAUUSD", "USDJPY", "EURUSD", "GBPUSD"]
    st = {r["id"].split("-")[0]: r["status"] for r in res}
    assert st["XAUUSD"] == "TAKEN" and st["USDJPY"] == "TAKEN" and st["GBPUSD"] == "SKIPPED"
    assert any(e["message"].startswith("SELECTED XAUUSD") and "score 91" in e["message"] for e in bus.recent())
    assert any("SKIPPED GBPUSD score 62 (below min" in e["message"] for e in bus.recent())


def test_ceiling_is_a_ceiling():
    co, book, w, bus = coord_env({"guardian": {"max_concurrent_positions": 2}})
    res = co.select(cands(), w.now)
    taken = [r for r in res if r["status"] == "TAKEN"]
    assert len(taken) == 2
    assert [r for r in res if r["status"] == "BLOCKED" and "max concurrent" in r["reason"]]


def test_risk_cap_sizes_down_lower_ranked():
    co, book, w, bus = coord_env({"guardian": {"max_open_risk_pct": 0.8}})
    res = co.select(cands()[:3], w.now)
    trades = sorted(book.open.values(), key=lambda t: -t["score"])
    assert len(trades) >= 2
    assert trades[0]["risk_money"] > trades[1]["risk_money"]   # second sized down by the cap
    assert any("sized down" in e["message"] for e in bus.recent())


def test_correlation_filter_blocks_second_correlated():
    co, book, w, bus = coord_env()
    eur = make_candidate("EURUSD", entry=1.08508, stop=1.08408, tp1=1.08608, tp2=1.08708, score=88)
    gbp = make_candidate("GBPUSD", entry=1.2701, stop=1.2690, tp1=1.2712, tp2=1.2730, score=80)
    res = co.select([eur, gbp], w.now)
    assert res[0]["status"] == "TAKEN" and res[1]["status"] == "BLOCKED" and "correlated" in res[1]["reason"]


# ------------------------------------------------------------------ accounts: reconnect / pause / views
def test_reconnect_with_backoff_and_states():
    cfg = make_cfg(accounts=[{"name": "M1", "kind": "mt5", "login": 1}])
    st = Storage(":memory:")
    now = [1000.0]
    am = AccountsManager(LogBus(st), cfg, st, clock=lambda: now[0])
    mock = MockAdapter(symbols=["EURUSD"], minutes=2000)
    mock.fail_connect = 2
    proxy = WorkerProxy(cfg.account("M1"), inline_adapter=mock)
    h = AccountHandle(cfg.account("M1"), proxy, None)
    am.add(h)
    am.connect_all()
    assert not h.connected and h.next_retry == pytest.approx(1001.0)
    am.sync(1000.5)
    assert h.snapshot.status == "DISCONNECTED" and h.attempts == 1
    am.sync(1001.0)       # retry #2 fails -> backoff 2s
    assert h.attempts == 2 and h.next_retry == pytest.approx(1003.0)
    am.sync(1003.0)       # retry #3 succeeds
    assert h.connected
    am.sync(1003.5)
    assert h.snapshot.status == "OK" and h.snapshot.balance == 10000.0
    v = am.view(h, 1003.5 + 7)
    assert v["real"]["status"] == "STALE"   # > 6 s since last good sync
    assert am.pause("M1") and h.paused
    am.pause_all(False)
    assert not h.paused


def test_totals_never_mix_currencies():
    from aurum_nexus.engine import Engine
    from aurum_nexus.data.feed import PriceFeed
    cfg = make_cfg(accounts=[{"name": "A", "kind": "paper_only", "currency": "USD"},
                             {"name": "B", "kind": "paper_only", "currency": "EUR", "starting_balance": 5000}])
    mock = MockAdapter(symbols=["XAUUSD", "EURUSD", "GBPUSD", "USDJPY"], minutes=3000)
    feed = PriceFeed(cfg, simulated=mock, clock=lambda: mock.now)
    eng = Engine(cfg, feed, storage=Storage(":memory:"), clock=lambda: mock.now, background=False)
    eng.cycle()
    pt = eng.state_cache["paper_totals"]
    assert set(pt) == {"USD", "EUR"} and pt["EUR"]["balance"] == 5000
