import pytest

from helpers import WED_10UTC, World, bus_and_book, make_candidate, make_cfg, make_ctx, make_handle
from aurum_nexus.bots.guardian import Guardian
from aurum_nexus.bots.risk import RiskManager
from aurum_nexus.bots.strategist import default_profile


def setup(cfg=None, now=WED_10UTC):
    cfg = cfg or make_cfg()
    st, bus, book = bus_and_book()
    w = World(now=now)
    g = Guardian(bus, cfg, book, news=None, risk=RiskManager(bus, cfg), clock=w.clock)
    h = make_handle(cfg, w)
    return cfg, g, h, w, book


def check(g, h, w, c=None, sym="XAUUSD", selected=None, risk_pct=0.5, ctx=None):
    c = c or make_candidate(sym)
    return g.check(c, h, ctx or make_ctx(c.symbol, w), default_profile(c.symbol), w.now, selected or [], None, risk_pct)


def add_trade(book, sym="XAUUSD", direction="long", acc="P1", open_ts=WED_10UTC - 3600, r=None, exit_type=None,
              close_ts=None, risk=50.0):
    t = {"id": book.new_id(), "account": acc, "mode": "paper", "symbol": sym, "direction": direction,
         "setup": "x", "timeframe": "M5", "open_ts": open_ts, "entry": 100.0, "stop": 99.0, "initial_stop": 99.0,
         "tp1": 101, "tp2": 102, "volume": 1.0, "volume_open": 1.0 if r is None else 0.0, "status": "open",
         "risk_money": risk, "ticket": 1, "meta": {}}
    if r is None:
        book.add(t)
    else:
        t.update(status="closed", r_multiple=r, exit_type=exit_type, close_ts=close_ts or open_ts + 600)
        book.close(t)
    return t


def test_allows_clean_setup():
    cfg, g, h, w, book = setup()
    v = check(g, h, w)
    assert v["allowed"], v["reason"]


def test_kill_switch_and_pause():
    cfg, g, h, w, book = setup()
    g.set_kill(True)
    assert check(g, h, w)["reason"] == "KILL SWITCH active"
    g.set_kill(False)
    h.paused = True
    assert check(g, h, w)["reason"] == "account paused"


def test_live_refused_on_preview():
    cfg, g, h, w, book = setup()
    h.live = True
    v = check(g, h, w, ctx=make_ctx("XAUUSD", w, preview=True))
    assert not v["allowed"] and "preview" in v["reason"]


def test_score_floor_and_dxy_block():
    cfg, g, h, w, book = setup()
    assert "below min" in check(g, h, w, make_candidate(score=65))["reason"]
    c = make_candidate()
    c.modifiers["dxy_block"] = 1
    assert "DXY" in check(g, h, w, c)["reason"]


def test_daily_max_loss_lockout():
    cfg, g, h, w, book = setup()
    h.paper.balance -= 250  # realized loss today recorded as a deal
    from aurum_nexus.models import DealInfo
    h.paper.deal_log.append(DealInfo(1, 1, w.now - 60, "XAUUSD", "sell", "out", 0.1, 2390, -250.0))
    v = check(g, h, w)
    assert not v["allowed"] and "daily max loss" in v["reason"]
    assert "P1" in g.lockouts


def test_max_trades_per_day_and_concurrency():
    cfg, g, h, w, book = setup(make_cfg({"guardian": {"max_trades_per_day": 2, "max_trades_per_hour": 10}}))
    add_trade(book, "EURUSD", r=0.5, open_ts=w.now - 7200)
    add_trade(book, "GBPUSD", r=0.5, open_ts=w.now - 7000)
    assert "max trades/day" in check(g, h, w)["reason"]
    cfg, g, h, w, book = setup(make_cfg({"guardian": {"max_concurrent_positions": 1}}))
    add_trade(book, "USDJPY", open_ts=w.now - 7200)
    assert "max concurrent" in check(g, h, w)["reason"]


def test_consecutive_losses_lockout_and_revenge():
    cfg, g, h, w, book = setup(make_cfg({"guardian": {"max_trades_per_hour": 50, "symbol_cooldown_minutes": 0}}))
    for i in range(4):
        add_trade(book, "EURUSD", r=-1.0, exit_type="sl", open_ts=w.now - 20000 + i * 1000, close_ts=w.now - 19000 + i * 1000)
    assert "consecutive losses" in check(g, h, w)["reason"]
    cfg, g, h, w, book = setup(make_cfg({"guardian": {"symbol_cooldown_minutes": 0}}))
    add_trade(book, "XAUUSD", r=-1.0, exit_type="sl", open_ts=w.now - 3000, close_ts=w.now - 600)
    assert "revenge block" in check(g, h, w)["reason"]


def test_correlation_and_same_symbol():
    cfg, g, h, w, book = setup()
    add_trade(book, "EURUSD", "long", open_ts=w.now - 7200)
    v = check(g, h, w, make_candidate("GBPUSD", entry=1.2701, stop=1.2690, tp1=1.2712, tp2=1.2730))
    assert not v["allowed"] and "correlated" in v["reason"]
    v = check(g, h, w, make_candidate("EURUSD", entry=1.0851, stop=1.0840, tp1=1.0862, tp2=1.0880))
    assert "already long EURUSD" in v["reason"]


def test_spread_filter():
    cfg, g, h, w, book = setup()
    w.prices["XAUUSD"] = (2400.0, 2402.0)       # 2.00 spread vs ATR(M5) 1.44
    assert "spread" in check(g, h, w)["reason"]


def test_open_risk_cap_size_down_or_skip():
    cfg, g, h, w, book = setup(make_cfg({"guardian": {"max_open_risk_pct": 1.0}}))
    add_trade(book, "USDJPY", open_ts=w.now - 7200, risk=60.0)   # 0.6% of 10k in use
    v = check(g, h, w, risk_pct=0.6)                              # room 0.4 -> 67% of planned size
    assert v["allowed"] and v["size_factor"] == pytest.approx(0.4 / 0.6)
    v = check(g, h, w, risk_pct=1.0)                              # room 0.4 -> 40% < 50% -> skip
    assert not v["allowed"] and "open-risk cap" in v["reason"]


def test_session_weekend_and_news():
    cfg, g, h, w, book = setup(now=WED_10UTC - 8 * 3600)   # 02:00 UTC Wed - Asia, gold profile blocks
    assert "outside allowed sessions" in check(g, h, w)["reason"]
    cfg, g, h, w, book = setup(now=WED_10UTC + 3 * 86400)  # Saturday
    assert "weekend" in check(g, h, w)["reason"]

    class News:
        def blackout(self, s, now):
            return {"title": "Non-Farm Employment Change", "country": "USD"}

        def unavailable_blocks(self):
            return False
    cfg, g, h, w, book = setup()
    g.news = News()
    assert "news blackout" in check(g, h, w)["reason"]


def test_profit_lock_blocks():
    cfg, g, h, w, book = setup(make_cfg({"guardian": {"max_trades_per_hour": 50, "symbol_cooldown_minutes": 0}}))
    for i in range(3):
        add_trade(book, "EURUSD", r=1.1, open_ts=w.now - 9000 + i * 1000, close_ts=w.now - 8000 + i * 1000)
    assert "profit lock" in check(g, h, w)["reason"]
