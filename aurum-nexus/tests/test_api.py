"""Hub API: first-run setup (setup code from LAN), login + rate limit, session cookie, REST data endpoints,
WebSocket push, Pause/Kill require login + CSRF header, TradingView webhook secret, JSON never contains NaN."""
import json

import pytest
from fastapi.testclient import TestClient

from aurum_nexus.config import config_from_dict
from aurum_nexus.data.feed import PriceFeed
from aurum_nexus.data.mock_adapter import MockAdapter
from aurum_nexus.engine import Engine
from aurum_nexus.hub.auth import Auth, hash_password, verify_password
from aurum_nexus.hub.server import _json, create_app
from aurum_nexus.storage import Storage

H = {"X-Requested-With": "AurumNexus"}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "tv-secret-123")
    monkeypatch.delenv("DASHBOARD_USER", raising=False)
    cfg = config_from_dict({"intel": {"cot": {"enabled": False}, "macro": {"enabled": False}},
                            "news": {"enabled": False}}, [{"name": "P1", "kind": "paper_only"}])
    mock = MockAdapter(symbols=["XAUUSD", "EURUSD", "GBPUSD", "USDJPY"], minutes=3000)
    feed = PriceFeed(cfg, simulated=mock, clock=lambda: mock.now)
    from aurum_nexus.intel.sources import IntelHub
    eng = Engine(cfg, feed, storage=Storage(":memory:"), clock=lambda: mock.now, background=False,
                 intel=IntelHub(cfg, clock=lambda: mock.now))
    eng.cycle()
    app = create_app(eng, cfg)
    return eng, app, TestClient(app)


def login(c):
    r = c.post("/api/setup", json={"username": "operator", "password": "correct-horse-9"})
    assert r.status_code == 200 and r.json()["ok"]


def test_password_hashing():
    h = hash_password("s3cret-pass")
    assert h.startswith("scrypt$") and "s3cret-pass" not in h
    assert verify_password("s3cret-pass", h) and not verify_password("wrong", h)


def test_pages_redirect_and_setup_flow(env):
    eng, app, c = env
    assert c.get("/", follow_redirects=False).headers["location"] == "/setup"
    assert c.get("/api/state").status_code == 401
    r = c.post("/api/setup", json={"username": "operator", "password": "short"})
    assert r.status_code == 400
    login(c)
    assert c.get("/", follow_redirects=False).status_code == 200
    assert c.post("/api/setup", json={"username": "x2", "password": "another-pass-1"}).status_code == 400


def test_setup_from_lan_requires_code(env):
    eng, app, c = env
    auth: Auth = app.state.auth
    assert auth.create_first_user("operator", "correct-horse-9", None, is_local=False) is not None
    assert auth.create_first_user("operator", "correct-horse-9", auth.setup_code, is_local=False) is None


def test_login_rate_limit_and_logout(env):
    eng, app, c = env
    login(c)
    c.post("/api/logout")
    assert c.get("/api/state").status_code == 401
    for _ in range(5):
        assert c.post("/api/login", json={"username": "operator", "password": "bad"}).status_code == 401
    r = c.post("/api/login", json={"username": "operator", "password": "correct-horse-9"})
    assert r.status_code == 429 and "too many attempts" in r.json()["error"]


def test_state_chart_bot_endpoints(env):
    eng, app, c = env
    login(c)
    s = c.get("/api/state").json()
    assert s["app"] == "Aurum Nexus" and s["min_rr"] == 1.0 and len(s["bots"]) == 9
    assert "NaN" not in c.get("/api/state").text
    ch = c.get("/api/chart?symbol=XAUUSD&tf=M5").json()
    assert ch["candles"] and "zones" in ch["layers"] and "volume_profile" in ch["layers"]
    assert c.get("/api/chart?symbol=BTCUSD&tf=M5").status_code == 400
    b = c.get("/api/bot/guardian").json()
    assert b["name"] == "GUARDIAN" and "config" in b
    assert c.get("/api/lan").json()["qr"].startswith("data:image/png;base64,")
    assert isinstance(c.get("/api/equity").json(), list)


def test_controls_require_login_and_csrf_header(env):
    eng, app, c = env
    assert c.post("/api/control/kill", json={"on": True}, headers=H).status_code == 401
    login(c)
    assert c.post("/api/control/kill", json={"on": True}).status_code == 403          # no CSRF header
    assert c.post("/api/control/kill", json={"on": True}, headers=H).json()["kill_switch"] is True
    assert eng.guardian.kill_switch
    c.post("/api/control/kill", json={"on": False}, headers=H)
    assert c.post("/api/control/pause", json={"account": "P1", "paused": True}, headers=H).json()["ok"]
    assert eng.accounts.handles["P1"].paused
    c.post("/api/control/pause", json={"account": "*", "paused": False}, headers=H)
    assert not eng.accounts.handles["P1"].paused


def test_api_token(env, monkeypatch):
    eng, app, c = env
    app.state.auth.api_token = "tok-xyz-123"
    assert c.get("/api/state", headers={"Authorization": "Bearer tok-xyz-123"}).status_code == 200
    assert c.get("/api/state?token=nope").status_code == 401


def test_websocket_push(env):
    eng, app, c = env
    with pytest.raises(Exception):
        with c.websocket_connect("/ws") as ws:
            ws.receive_text()
    login(c)
    with c.websocket_connect("/ws") as ws:
        first = json.loads(ws.receive_text())
        assert first["type"] == "events"
        second = json.loads(ws.receive_text())
        assert second["type"] in ("state", "events")


def test_tradingview_webhook(env):
    eng, app, c = env
    assert c.post("/api/webhook/tradingview", json={"secret": "wrong", "symbol": "XAUUSD", "direction": "buy"}).status_code == 403
    r = c.post("/api/webhook/tradingview", json={"secret": "tv-secret-123", "symbol": "OANDA:XAUUSD", "direction": "buy",
                                                  "message": "breakout"})
    assert r.status_code == 200
    assert eng.intel.tv_alerts[-1]["direction"] == "long"
    m = eng.intel.score_modifier("XAUUSD", "long")
    assert m["points"] == 1.5 and "TradingView alert long" in m["notes"]


def test_json_sanitizer():
    import numpy as np
    out = json.loads(_json({"a": float("nan"), "b": np.float64(1.5), "c": [np.int64(2), float("inf")]}))
    assert out == {"a": None, "b": 1.5, "c": [2, None]}
