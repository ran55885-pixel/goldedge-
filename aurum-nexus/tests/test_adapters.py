"""Adapters: real Mt5Adapter field mapping vs a strict fake of the official package, OANDA v20 against
spec-conformant sample responses, keyless fallback parsing against REAL saved responses, live refusal."""
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from fake_mt5 import FakeMT5, TradeDeal, TradePosition
from aurum_nexus.config import AccountConfig, LIVE_CONFIRM_PHRASE, config_from_dict, effective_live
from aurum_nexus.data.fallback_adapter import FallbackPublicAdapter, align_bars, parse_swissquote, parse_yahoo
from aurum_nexus.data.mt5_adapter import Mt5Adapter
from aurum_nexus.data.oanda_adapter import OandaAdapter, parse_candles, parse_price, parse_time

DATA = Path(__file__).parent / "data"


@pytest.fixture
def mt5():
    return FakeMT5(server_offset_h=2.0)


@pytest.fixture
def adapter(mt5, monkeypatch):
    monkeypatch.setenv("MT5_PASSWORD_T1", "pw-not-logged")
    acc = AccountConfig(name="T1", login=5550001, server="FakeBroker-Demo", terminal_path="C:/MT5/t1/terminal64.exe",
                        password_env="MT5_PASSWORD_T1", server_utc_offset_hours=2.0)
    a = Mt5Adapter(acc, mt5_module=mt5, suffix_candidates=["", ".m", "m"], bot_magic=770077)
    assert a.connect()
    return a


# ------------------------------------------------------------------ MT5
def test_mt5_initialize_portable_and_secret(adapter, mt5):
    kw = mt5.init_kwargs
    assert kw["path"].endswith("terminal64.exe") and kw["portable"] is True
    assert kw["login"] == 5550001 and kw["server"] == "FakeBroker-Demo" and kw["password"] == "pw-not-logged"


def test_mt5_suffix_resolution_and_spec(adapter):
    assert adapter.resolve_symbol("XAUUSD") == "XAUUSD.m"
    sp = adapter.symbol_spec("XAUUSD")
    assert sp.broker_name == "XAUUSD.m" and sp.tick_size == 0.01 and sp.tick_value == 1.0 and sp.contract_size == 100
    assert sp.volume_min == 0.01 and sp.volume_step == 0.01 and sp.filling_mode == 1
    assert adapter.resolve_symbol("GBPUSD") is None


def test_mt5_tick_time_is_converted_to_utc(adapter):
    t = adapter.tick("EURUSD")
    assert t.time == pytest.approx(1_700_000_000.25)
    assert t.bid == 1.085 and t.ask == 1.08508


def test_mt5_rates_mapping(adapter):
    b = adapter.bars("EURUSD", "M5", 20)
    assert list(b.columns) == ["time", "open", "high", "low", "close", "tick_volume", "real_volume", "spread"]
    assert int(b["time"].iloc[0]) == 1_700_000_000
    assert float(b["tick_volume"].iloc[0]) == 100


def test_mt5_account_mapping(adapter):
    a = adapter.account()
    assert a.status == "OK" and a.login == 5550001 and a.trade_mode == "DEMO" and a.currency == "USD"
    assert a.leverage == 500 and a.margin_free == 9880.0 and a.server == "FakeBroker-Demo"


def test_mt5_positions_tags_and_deals(adapter, mt5):
    base = 1_700_000_000 + 7200
    mt5.positions = [
        TradePosition(1, base, 0, base, 0, 0, 770077, 1, 0, 0.1, 2340.0, 2330.0, 2370.0, 2350.1, 0.0, 101.0, "XAUUSD.m", "AN", ""),
        TradePosition(2, base, 0, base, 0, 1, 0, 2, 0, 0.2, 1.09, 0.0, 0.0, 1.085, 0.0, 100.0, "EURUSD.m", "", ""),
        TradePosition(3, base, 0, base, 0, 0, 12345, 3, 0, 0.1, 150.0, 0.0, 0.0, 150.0, 0.0, 0.0, "USDJPY.m", "", ""),
    ]
    ps = adapter.positions()
    assert [p.tag for p in ps] == ["BOT", "MANUAL", "OTHER"]
    assert ps[0].side == "buy" and ps[1].side == "sell" and ps[0].time == 1_700_000_000
    mt5.deals = [
        TradeDeal(10, 1, base + 60, 0, 0, 1, 770077, 1, 0, 0.1, 2345.0, -0.7, 0.0, 50.0, 0.0, "XAUUSD.m", "", ""),
        TradeDeal(11, 2, base - 86400 * 3, 0, 0, 1, 0, 2, 0, 0.1, 1.0, 0.0, 0.0, 999.0, 0.0, "EURUSD.m", "", ""),
    ]
    ds = adapter.deals(1_700_000_000, 1_700_000_000 + 3600)
    assert len(ds) == 1 and ds[0].net == pytest.approx(49.3) and ds[0].entry == "out"
    f, t = mt5.last_deals_range
    assert isinstance(f, datetime) and f.tzinfo == timezone.utc


def test_mt5_order_send_request_fields(adapter, mt5):
    r = adapter.market_order("XAUUSD", "buy", 0.1, 2340.0, 2370.0, 20, 770077, "AN test", 1)
    assert r.ok and r.retcode == 10009 and r.filling == "IOC"
    req = mt5.sent[-1]
    assert req["type"] == 0 and req["price"] == 2350.35 and req["type_filling"] == 1 and req["magic"] == 770077
    mt5.next_retcode = [10004]
    r2 = adapter.market_order("XAUUSD", "sell", 0.1, 2360.0, 2330.0, 20, 770077, "AN", 0)
    assert not r2.ok and r2.retcode == 10004


def test_mt5_market_book(adapter):
    b = adapter.market_book("XAUUSD")
    assert {x["side"] for x in b} == {"buy", "sell"}


def test_mt5_password_never_in_status(adapter):
    assert "pw-not-logged" not in json.dumps(adapter.status())


def test_mt5_missing_package():
    acc = AccountConfig(name="X")
    a = Mt5Adapter(acc, mt5_module=None)
    a.mt5 = None
    assert not a.connect() and "Windows" in a.last_error


# ------------------------------------------------------------------ OANDA (spec-conformant samples)
CANDLES = {"instrument": "XAU_USD", "granularity": "M1", "candles": [
    {"complete": True, "volume": 42, "time": "2026-11-20T09:40:00.000000000Z",
     "bid": {"o": "3589.10", "h": "3590.00", "l": "3588.50", "c": "3589.70"},
     "mid": {"o": "3589.25", "h": "3590.15", "l": "3588.65", "c": "3589.85"},
     "ask": {"o": "3589.40", "h": "3590.30", "l": "3588.80", "c": "3590.00"}},
    {"complete": False, "volume": 7, "time": "2026-11-20T09:41:00.000000000Z",
     "mid": {"o": "3589.85", "h": "3590.05", "l": "3589.60", "c": "3589.90"}}]}
PRICE = {"type": "PRICE", "instrument": "XAU_USD", "time": "2026-11-20T09:40:26.123456789Z", "tradeable": True,
         "bids": [{"price": "3589.70", "liquidity": 1000}], "asks": [{"price": "3590.05", "liquidity": 1000}],
         "closeoutBid": "3589.70", "closeoutAsk": "3590.05"}


def test_oanda_parsers():
    df = parse_candles(CANDLES)
    assert len(df) == 2 and df["close"].iloc[0] == pytest.approx(3589.85)
    assert df["spread"].iloc[0] == pytest.approx(0.30) and df["tick_volume"].iloc[0] == 42
    t = parse_price(PRICE)
    assert t.symbol == "XAUUSD" and t.bid == 3589.70 and t.ask == 3590.05
    assert parse_time("2026-11-20T09:40:26.123456789Z") == pytest.approx(
        datetime(2026, 11, 20, 9, 40, 26, 123456, tzinfo=timezone.utc).timestamp())


def test_oanda_http_and_stream_lines():
    def handler(req: httpx.Request):
        assert req.headers["Authorization"] == "Bearer tok-123"
        if req.url.path.endswith("/candles"):
            assert req.url.params["granularity"] == "M5"
            return httpx.Response(200, json=CANDLES)
        if req.url.path.endswith("/pricing"):
            return httpx.Response(200, json={"prices": [PRICE]})
        if req.url.path.endswith("/summary"):
            return httpx.Response(200, json={"account": {"id": "001"}})
        return httpx.Response(404)
    o = OandaAdapter("tok-123", "001-001-1-001", "practice", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert o.connect() and o.api == "https://api-fxpractice.oanda.com"
    assert len(o.bars("XAUUSD", "M5", 10)) == 2
    assert o.tick("XAUUSD").ask == 3590.05
    assert o.handle_stream_line('{"type":"HEARTBEAT","time":"2026-11-20T09:40:30.000000000Z"}') is None
    assert o.handle_stream_line(json.dumps(PRICE)).bid == 3589.70


def test_oanda_unconfigured_degrades():
    o = OandaAdapter("", "", "practice")
    assert not o.connect() and "OANDA_TOKEN" in o.last_error


# ------------------------------------------------------------------ fallback (REAL saved samples)
def test_swissquote_real_sample():
    t = parse_swissquote(json.loads((DATA / "swissquote_xauusd.json").read_text()), "XAUUSD")
    assert t is not None and t.ask > t.bid > 100 and t.source == "Swissquote public"


def test_yahoo_real_sample_and_alignment():
    df, meta = parse_yahoo(json.loads((DATA / "yahoo_gcf_5m.json").read_text()))
    assert len(df) > 10 and meta["symbol"] == "GC=F"
    al = align_bars(df, 300)
    assert (al["time"] % 300 == 0).all()


def test_fallback_is_preview_and_labelled():
    def handler(req):
        if "swissquote" in req.url.host:
            return httpx.Response(200, json=json.loads((DATA / "swissquote_xauusd.json").read_text()))
        return httpx.Response(200, json=json.loads((DATA / "yahoo_gcf_5m.json").read_text()))
    f = FallbackPublicAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert f.is_preview is True
    f.tick("XAUUSD")
    b = f.bars("XAUUSD", "M5", 50)
    assert len(b) > 0
    assert f.preview_label("XAUUSD").startswith("PREVIEW ONLY · not for trading")
    assert f.has_volume("EURUSD") is False   # Yahoo FX has no volume -> volume tools disabled


# ------------------------------------------------------------------ live gating / refusal on fallback
def test_live_requires_every_gate():
    acc = AccountConfig(name="R", kind="mt5", mode="live", allow_live=True, allow_real=False)
    cfg = config_from_dict({"system": {"mode": "live"}})
    assert effective_live(cfg, acc, "DEMO", False)[0] is False  # not confirmed
    cfg.live_confirmed = True
    assert effective_live(cfg, acc, "DEMO", False) == (True, "live")
    assert effective_live(cfg, acc, "REAL", False)[0] is False   # real needs allow_real
    acc.allow_real = True
    assert effective_live(cfg, acc, "REAL", False)[0] is True
    ok, why = effective_live(cfg, acc, "REAL", True)
    assert ok is False and "fallback" in why                      # hard refusal on preview data
    assert effective_live(cfg, acc, None, False)[0] is True        # unknown treated as REAL (allowed here)
    acc.allow_real = False
    assert effective_live(cfg, acc, None, False)[0] is False       # unknown -> REAL -> blocked
    assert LIVE_CONFIRM_PHRASE == "I UNDERSTAND"
    paper = config_from_dict({"system": {"mode": "paper"}})
    paper.live_confirmed = True
    assert effective_live(paper, acc, "DEMO", False)[0] is False
