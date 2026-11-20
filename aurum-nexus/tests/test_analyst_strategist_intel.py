import json
from pathlib import Path

import httpx
import pytest
import yaml

from helpers import WED_10UTC, make_cfg
from aurum_nexus.bots.analyst import Analyst, stats
from aurum_nexus.bots.strategist import Strategist, clamp_profile, default_profile
from aurum_nexus.intel.news import NewsCalendar, parse_events, symbol_currencies
from aurum_nexus.intel.sources import IntelHub, dom_imbalance, fetch_cot, parse_cot, parse_fred_csv, yield_trend
from aurum_nexus.logbus import LogBus
from aurum_nexus.storage import Storage
from aurum_nexus.tradebook import TradeBook

DATA = Path(__file__).parent / "data"


# ------------------------------------------------------------------ Analyst
def mk_trades(rs, setup="orb", symbol="XAUUSD", start=WED_10UTC):
    return [{"id": f"t{i}", "account": "P1", "mode": "paper", "symbol": symbol, "setup": setup, "session": "london",
             "timeframe": "M5", "exit_type": "tp1", "open_ts": start + i * 3600, "close_ts": start + i * 3600 + 600,
             "r_multiple": r, "pnl": r * 50, "status": "closed", "direction": "long"} for i, r in enumerate(rs)]


def test_stats_math():
    s = stats(mk_trades([1, -1, 2, -1, 0.5]))
    assert s["trades"] == 5 and s["win_rate"] == 60.0
    assert s["expectancy_r"] == pytest.approx(0.3) and s["profit_factor"] == pytest.approx(3.5 / 2)
    assert s["max_dd_r"] == pytest.approx(1.0)


def analyst_with(trades, cfg=None):
    st = Storage(":memory:")
    book = TradeBook(st)
    for t in trades:
        book.close(t)
    now = [WED_10UTC + 200 * 3600]
    a = Analyst(LogBus(st), cfg or make_cfg(), book, st, clock=lambda: now[0])
    return a, now


def test_adaptive_weight_needs_min_sample_and_is_bounded():
    a, now = analyst_with(mk_trades([3.0] * 14))
    a.refresh(now[0], force=True)
    assert a.setup_adjust("orb") == 0.0            # 14 < 15 -> no adaptation
    a, now = analyst_with(mk_trades([3.0] * 15))
    a.refresh(now[0], force=True)
    assert a.setup_adjust("orb") == 15.0           # bounded at +15


def test_negative_setup_probation_never_touches_safety():
    cfg = make_cfg()
    a, now = analyst_with(mk_trades([-1.0] * 20), cfg)
    a.refresh(now[0], force=True)
    assert a.setup_disabled("orb") and a.setup_adjust("orb") == -15.0
    now[0] += 49 * 3600
    assert not a.setup_disabled("orb")              # probation over (48h)
    assert cfg.min_rr == 1.0 and cfg.min_score == 70


def test_seasonality_shrinkage_bounded():
    trades = mk_trades([2.0] * 3) + mk_trades([-0.5] * 30, setup="zone_reaction", start=WED_10UTC + 86400 * 2)
    a, now = analyst_with(trades)
    a.refresh(now[0], force=True)
    m = a.seasonality_modifier("XAUUSD", "orb", 10, 2)
    assert 0 < m <= 5
    hm = a.report["heatmaps"]["XAUUSD"]
    assert len(hm) == 7 and len(hm[0]) == 24


def test_frequency_monitor_suggests_only():
    st = Storage(":memory:")
    book = TradeBook(st)
    for d in range(6):
        st.execute("INSERT INTO signals (id, ts, symbol, score) VALUES (?,?,?,?)", (f"s{d}", WED_10UTC + d * 86400, "XAUUSD", 80))
    a = Analyst(LogBus(st), make_cfg(), book, st, clock=lambda: WED_10UTC + 6 * 86400)
    rep = a.refresh(force=True, contexts_symbols=["XAUUSD"])
    assert rep["frequency"]["XAUUSD"]["days_below_target"] >= 5
    assert a.warnings and "suggestion only" in a.suggestions[0]


# ------------------------------------------------------------------ Strategist
def strat(tmp_path, live=False):
    st = Storage(":memory:")
    s = Strategist(LogBus(st), make_cfg(), st, profiles_dir=tmp_path)
    s.load(["XAUUSD", "USDJPY"])
    s.live_mode = live
    return s, st


def test_default_profiles_documented_differences():
    g, j = default_profile("XAUUSD"), default_profile("USDJPY")
    assert g["playbooks"]["sweep_reversal"]["weight"] > 1 and g["playbooks"]["orb"]["weight"] > 1
    assert "asia" not in g["sessions_allowed"] and "asia" in j["sessions_allowed"]
    assert g["stops"]["atr_buffer_mult"] > j["stops"]["atr_buffer_mult"]
    assert default_profile("EURUSD")["playbooks"]["bos_retest"]["weight"] > 1


def test_clamp_and_forbidden_keys():
    p = default_profile("XAUUSD")
    p["targets"]["tp1_rr"] = 0.5
    p["playbooks"]["orb"]["weight"] = 9
    p["min_rr"] = 0.5
    p["min_score_offset"] = -10
    q, warns = clamp_profile(p)
    assert q["targets"]["tp1_rr"] == 1.0 and q["playbooks"]["orb"]["weight"] == 1.5
    assert "min_rr" not in q and q["min_score_offset"] == 0 and len(warns) >= 3


def test_auto_apply_in_paper_and_approval_in_live(tmp_path):
    s, st = strat(tmp_path)
    r = s.propose("XAUUSD", {"playbooks": {"orb": {"weight": 1.3}}}, "test", "unit")
    assert r["status"] == "applied" and s.profile("XAUUSD")["version"] == 2
    saved = yaml.safe_load((tmp_path / "XAUUSD.yaml").read_text())
    assert saved["playbooks"]["orb"]["weight"] == 1.3
    s.live_mode = True
    r = s.propose("XAUUSD", {"stops": {"atr_buffer_mult": 0.4}}, "wf", "optimizer")
    assert r["status"] == "pending"
    assert s.apply(r["id"], by="user")["status"] == "applied"
    assert s.profile("XAUUSD")["stops"]["atr_buffer_mult"] == 0.4
    assert st.query("SELECT COUNT(*) n FROM profile_changes")[0]["n"] == 2


def test_lock_and_safety_keys(tmp_path):
    s, st = strat(tmp_path)
    s.set_lock("USDJPY", True)
    r = s.propose("USDJPY", {"targets": {"tp1_rr": 1.5}}, "x", "unit")
    assert r["status"] == "pending" and r["reason"] == "profile locked"
    r = s.propose("USDJPY", {"min_rr": 0.5}, "x", "unit")
    assert r["status"] == "rejected"
    r = s.propose("USDJPY", {"guardian": {"daily_max_loss_pct": 9}}, "x", "unit")
    assert r["status"] == "rejected"


# ------------------------------------------------------------------ Intel (real saved samples; mocked HTTP)
def test_news_parse_and_blackout():
    evs = parse_events(json.loads((DATA / "ff_calendar_sample.json").read_text()))
    assert evs and all("ts" in e for e in evs)
    cfg = make_cfg()
    nc = NewsCalendar(cfg)
    nc.events = [{"title": "CPI m/m", "country": "USD", "ts": 1000.0, "impact": "High"}]
    assert nc.blackout("XAUUSD", 1000 - 29 * 60)["title"] == "CPI m/m"
    assert nc.blackout("XAUUSD", 1000 - 31 * 60) is None
    assert nc.blackout("EURJPY", 1000) is None
    assert symbol_currencies("XAUUSD") == {"USD"}
    assert nc.upcoming("EURUSD", 1000 - 5 * 60, 10)["minutes"] == pytest.approx(5)


def test_news_manual_blackout_and_unavailable():
    cfg = make_cfg({"news": {"manual_blackouts": [{"start": "2023-11-15T13:00:00Z", "end": "2023-11-15T14:00:00Z",
                                                   "currencies": ["USD"], "title": "FOMC (manual)"}]}})
    def boom(req):
        return httpx.Response(503)
    nc = NewsCalendar(cfg, client=httpx.Client(transport=httpx.MockTransport(boom)))
    nc.refresh(force=True)
    assert nc.status == "unavailable" and not nc.unavailable_blocks()
    assert nc.blackout("EURUSD", 1_700_053_200 + 60)["title"] == "FOMC (manual)"


def test_cot_fred_parse_and_degrade():
    cot = parse_cot(json.loads((DATA / "cot_gold_sample.json").read_text()))
    assert cot and 0 <= cot["percentile"] <= 100 and cot["source"].startswith("CFTC")
    obs = parse_fred_csv((DATA / "fred_dfii10_sample.csv").read_text())
    assert len(obs) > 30 and yield_trend(obs)["trend"] in ("rising", "falling", "flat")
    cli = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(RuntimeError):
        fetch_cot(cli)
    hub = IntelHub(make_cfg(), client=cli)
    hub.refresh(["XAUUSD"])                      # never raises
    assert hub.cot is None and "cot" in hub.errors and hub.snapshot()["cot"] == "n/a"
    assert hub.score_modifier("XAUUSD", "long")["points"] == 0


def test_macro_and_dom_modifiers():
    hub = IntelHub(make_cfg(), clock=lambda: 100.0)
    hub.real_yield = {"trend": "rising", "change": 0.2}
    assert hub.score_modifier("XAUUSD", "long")["points"] < 0
    book = [{"side": "buy", "price": 99.9, "volume": 50}, {"side": "sell", "price": 100.1, "volume": 10}]
    d = dom_imbalance(book, 100.0, 0.5)
    assert d["imbalance"] == pytest.approx(40 / 60, abs=1e-3)
    hub.update_dom("EURUSD", book, 100.0, 1.0)
    assert hub.score_modifier("EURUSD", "long")["points"] == 1
