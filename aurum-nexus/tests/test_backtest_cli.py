"""Backtester (identical pipeline, intra-bar path, costs, report, RR assertion, time-target comparison,
trades/day), walk-forward optimiser, CSV format, CLI commands (check / live confirmation / download)."""
import json
from pathlib import Path

import httpx
import pandas as pd
import pytest

from aurum_nexus.backtest import csvio
from aurum_nexus.backtest.engine import BacktestAdapter, _intrabar, run_backtest
from aurum_nexus.backtest.optimizer import objective, walk_forward, write_optimize_report
from aurum_nexus.backtest.report import write_report
from aurum_nexus.backtest.synthetic import generate_m1
from aurum_nexus.bots.analyst import stats
from aurum_nexus.cli import confirm_live, main
from aurum_nexus.config import config_from_dict
from aurum_nexus.data.history import HistoryStore

START = 1_699_833_600   # Monday 2023-11-13 00:00 UTC


@pytest.fixture(scope="module")
def data():
    syms = ["XAUUSD", "EURUSD", "USDJPY", "GBPUSD"]
    return {s: generate_m1(s, START, 4 * 1440, seed=21 + i) for i, s in enumerate(syms)}


@pytest.fixture(scope="module")
def bt(data):
    cfg = config_from_dict({})
    return cfg, run_backtest(cfg, data, ["XAUUSD", "EURUSD"], step=5, warmup_days=2)


def test_backtest_runs_identical_pipeline(bt):
    cfg, res = bt
    assert res["symbols"] == ["XAUUSD", "EURUSD"] and res["equity"]
    assert res["min_planned_rr"] is None or res["min_planned_rr"] >= 1.0 - 1e-6
    for t in res["trades"]:
        assert t["status"] == "closed" and t["r_multiple"] is not None
        assert t["setup"] and t["timeframe"] in ("M1", "M5", "M15")
        assert t["pnl_net"] == pytest.approx(t["pnl"] - t["commission"])


def test_report_files_and_sections(bt, tmp_path):
    cfg, res = bt
    extra = {"time_target_comparison": {"time_targets ON (baseline)": stats(res["trades"])}}
    s = write_report(res, cfg, tmp_path, extra)
    for f in ("equity_curve.png", "trades.csv", "report.json", "report.md"):
        assert (tmp_path / f).exists()
    assert set(s) >= {"by_setup", "by_session", "by_symbol", "by_timeframe", "by_exit_type", "trades_per_day",
                      "is_oos", "walk_forward_windows", "rr_floor_ok"}
    assert s["rr_floor_ok"] is True
    md = (tmp_path / "report.md").read_text()
    assert "In-sample" in md and "Out-of-sample" in md and "Time targets" in md
    assert "No profit promise" in md


def test_time_targets_off_variant_runs(data):
    cfg = config_from_dict({})
    r = run_backtest(cfg, data, ["XAUUSD"], step=10, warmup_days=2, overrides={"time_targets": {"enabled": False}})
    assert all(t["exit_type"] != "time" for t in r["trades"])


def test_intrabar_sl_assumed_before_tp(data):
    """A bar touching both SL and TP closes the trade at the SL (conservative path)."""
    cfg = config_from_dict({})
    from aurum_nexus.backtest.engine import _cfg_for_backtest
    from aurum_nexus.data.feed import PriceFeed
    from aurum_nexus.engine import Engine
    from aurum_nexus.storage import Storage
    bc = _cfg_for_backtest(cfg, ["XAUUSD"], None, 10000)
    store = HistoryStore({"XAUUSD": data["XAUUSD"]})
    ad = BacktestAdapter(store, {"default": 10}, {"XAUUSD": True})
    bar = data["XAUUSD"].iloc[3000]
    ad.now = float(bar["time"]) + 66
    feed = PriceFeed(bc, simulated=ad, clock=lambda: ad.now)
    eng = Engine(bc, feed, storage=Storage(":memory:"), clock=lambda: ad.now, background=False)
    h = eng.accounts.handles["BACKTEST"]
    o = float(bar["open"])
    res = h.paper.market_order("XAUUSD", "buy", 0.1, 0, 0, 10, 770077, "t", 2)
    t = {"id": "x", "account": "BACKTEST", "mode": "paper", "symbol": "XAUUSD", "direction": "long",
         "setup": "s", "timeframe": "M5", "open_ts": ad.now - 600, "entry": o, "stop": float(bar["low"]) + 0.01,
         "initial_stop": float(bar["low"]) + 0.01, "tp1": float(bar["high"]) - 0.01, "tp2": float(bar["high"]) - 0.01,
         "volume": 0.1, "volume_open": 0.1, "ticket": res.order, "status": "open", "risk_money": 10, "meta": {}}
    eng.book.add(t)
    m1 = {"XAUUSD": data["XAUUSD"].set_index("time")}
    _intrabar(eng, ad, m1, int(bar["time"]), ad.now)
    assert t["status"] == "closed" and t["exit_type"] == "sl"


def test_csv_format_roundtrip(tmp_path):
    df = generate_m1("EURUSD", START, 120, seed=1)
    p = tmp_path / "EURUSD.csv"
    csvio.write_m1_csv(df, p, "SIMULATED")
    back = csvio.read_m1_csv(p)
    assert len(back) == 120 and int(back["time"].iloc[0]) == START
    assert p.read_text().startswith("# SIMULATED")
    bad = tmp_path / "bad.csv"
    bad.write_text("time,open,close\n2024-01-01 00:00:00,1,1\n")
    with pytest.raises(ValueError):
        csvio.read_m1_csv(bad)
    epoch = tmp_path / "XAUUSD.csv"
    epoch.write_text("time,open,high,low,close,tick_volume\n1700000000,1,2,0.5,1.5,10\n")
    assert int(csvio.read_m1_csv(epoch)["time"].iloc[0]) == 1_700_000_000


def test_optimizer_objective_and_bounds(data, tmp_path):
    assert objective({"trades": 2, "expectancy_r": 1.0}) == -1.0
    assert objective({"trades": 20, "expectancy_r": 0.3, "max_dd_r": 2}) == pytest.approx(0.3)
    with pytest.raises(ValueError):
        walk_forward(config_from_dict({}), data, "XAUUSD", {"min_rr": [0.5]})
    with pytest.raises(ValueError):
        walk_forward(config_from_dict({}), data, "XAUUSD", {"stops.atr_buffer_mult": [5.0]})


def test_walk_forward_small(data, tmp_path):
    cfg = config_from_dict({})
    res = walk_forward(cfg, {"XAUUSD": data["XAUUSD"]}, "XAUUSD", {"stops.atr_buffer_mult": [0.25, 0.45]},
                       train_days=1, test_days=0.5, step=15, warmup_days=1, log=lambda *a: None)
    assert res["windows"] and res["proposal"]["stops.atr_buffer_mult"] in (0.25, 0.45)
    out = write_optimize_report(res, None, tmp_path, register=False)
    assert (tmp_path / "proposed_XAUUSD.yaml").exists() and (tmp_path / "optimize_report.md").exists()


# ------------------------------------------------------------------ CLI
def test_confirm_live_requires_phrase():
    cfg = config_from_dict({"system": {"mode": "live"}}, [{"name": "A", "kind": "mt5", "mode": "live", "allow_live": True}])
    assert confirm_live(cfg, False, input_fn=lambda p: "yes") is False
    assert confirm_live(cfg, False, input_fn=lambda p: "I UNDERSTAND") is True
    assert confirm_live(cfg, True) is True
    paper = config_from_dict({}, [{"name": "A", "kind": "mt5"}])
    assert confirm_live(paper, True) is False


def test_cli_check_reports_clamps(tmp_path, capsys):
    (tmp_path / "profiles").mkdir()
    (tmp_path / "system.yaml").write_text("risk:\n  min_rr: 0.7\n  min_score: 50\n")
    (tmp_path / "accounts.yaml").write_text("accounts:\n  - name: P\n    kind: paper_only\n")
    assert main(["--config-dir", str(tmp_path), "check"]) == 0
    out = capsys.readouterr().out
    assert "min_rr=0.7 is below the hard floor" in out and "min_score=50.0 is below the hard floor" in out
    assert "min_rr=1.0" in out


def test_download_yahoo_mocked(tmp_path, monkeypatch):
    sample = json.loads((Path(__file__).parent / "data" / "yahoo_gcf_5m.json").read_text())
    from aurum_nexus import history_download as hd
    monkeypatch.setattr(hd.time, "sleep", lambda s: None)
    cli = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=sample)))
    paths = hd.download_yahoo(["XAUUSD"], tmp_path, days=7, client=cli, log=lambda *a: None)
    txt = paths[0].read_text()
    assert "PREVIEW/RESEARCH ONLY" in txt and "COMEX futures, not spot" in txt


def test_download_mt5_with_fake_terminal(tmp_path):
    from fake_mt5 import FakeMT5
    from aurum_nexus.config import AccountConfig
    from aurum_nexus import history_download as hd
    acc = AccountConfig(name="T", login=5550001, server="FakeBroker-Demo", server_utc_offset_hours=2)
    paths = hd.download_mt5(acc, ["EURUSD", "GBPUSD"], START, START + 86400, tmp_path, mt5_module=FakeMT5(),
                            suffixes=["", ".m"], log=lambda *a: None)
    assert [p.name for p in paths] == ["EURUSD.csv"]
    df = csvio.read_m1_csv(paths[0])
    assert int(df["time"].iloc[0]) == 1_700_000_000   # server time converted to UTC
