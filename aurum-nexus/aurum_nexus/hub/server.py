"""Hub process: builds the price feed + engine (+ one worker process per MT5 account), runs the engine loop
in a background thread and serves the AURUM NEXUS dashboard (FastAPI + WebSocket) on the LAN.

Security: every page and API needs a login session (or the optional DASHBOARD_TOKEN); Pause/Resume/Kill
and strategy approvals are POST-only, need a session and the X-Requested-With header (CSRF guard on top of
SameSite=Strict cookies). Bind 0.0.0.0 for phone access on the LAN; remote access only via Tailscale or a
Cloudflare Tunnel (HTTPS) - never port forwarding (see docs/REMOTE_ACCESS.md).
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import math
import os
import socket
import threading
import time
import webbrowser
from collections import deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from aurum_nexus import APP_NAME, __version__
from aurum_nexus.hub.auth import COOKIE, Auth
from aurum_nexus.hub.chartdata import build_chart

WEB = Path(__file__).resolve().parent.parent / "web"
log = logging.getLogger("aurum.hub")
LOCAL = {"127.0.0.1", "::1", "localhost", "testclient"}


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))   # no packet is sent; picks the LAN interface
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def qr_png(data: str) -> bytes:
    import qrcode
    img = qrcode.make(data, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _clean(o):
    """Make any engine structure JSON-safe: NaN/inf -> null, numpy -> python, objects -> dict."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, (str, int, bool)) or o is None:
        return o
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set, deque)):
        return [_clean(v) for v in o]
    if hasattr(o, "item") and not hasattr(o, "__len__"):
        return _clean(o.item())
    if hasattr(o, "tolist"):
        return _clean(o.tolist())
    if hasattr(o, "__dataclass_fields__"):
        return _clean({k: getattr(o, k) for k in o.__dataclass_fields__})
    if hasattr(o, "__dict__"):
        return _clean(vars(o))
    return str(o)


def _json(obj) -> str:
    return json.dumps(_clean(obj), separators=(",", ":"))


def create_app(engine, cfg, auth: Auth | None = None) -> FastAPI:
    app = FastAPI(title=APP_NAME, version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    auth = auth or Auth(engine.storage, cfg.get("dashboard.session_hours", 12),
                        cfg.get("dashboard.login_max_attempts", 5), cfg.get("dashboard.login_window_seconds", 300))
    app.state.auth = auth
    app.state.engine = engine
    app.mount("/static", StaticFiles(directory=str(WEB)), name="static")
    tv_secret = os.environ.get("TRADINGVIEW_WEBHOOK_SECRET") or None
    tv_limiter = auth.limiter.__class__(30, 60)

    def client_ip(req) -> str:
        return req.client.host if req.client else "?"

    def current_user(req: Request) -> str | None:
        u = auth.user_for(req.cookies.get(COOKIE))
        if u:
            return u
        authz = req.headers.get("authorization", "")
        tok = authz[7:] if authz.lower().startswith("bearer ") else req.query_params.get("token")
        return "api-token" if auth.check_api_token(tok) else None

    def require(req: Request) -> str:
        u = current_user(req)
        if not u:
            raise HTTPException(401, "login required")
        return u

    def require_control(req: Request) -> str:
        u = require(req)
        if req.headers.get("x-requested-with") != "AurumNexus" and u != "api-token":
            raise HTTPException(403, "missing X-Requested-With header")
        return u

    def jr(obj, status=200):
        return Response(_json(obj), status_code=status, media_type="application/json")

    # ------------------------------------------------------------------ pages
    @app.get("/", response_class=HTMLResponse)
    def index(req: Request):
        if auth.needs_setup:
            return RedirectResponse("/setup", 303)
        if not current_user(req):
            return RedirectResponse("/login", 303)
        return FileResponse(WEB / "index.html")

    @app.get("/login", response_class=HTMLResponse)
    def login_page():
        if auth.needs_setup:
            return RedirectResponse("/setup", 303)
        return FileResponse(WEB / "login.html")

    @app.get("/setup", response_class=HTMLResponse)
    def setup_page(req: Request):
        if not auth.needs_setup:
            return RedirectResponse("/login", 303)
        return FileResponse(WEB / "login.html")

    @app.get("/auth/launch")
    def launch(t: str = ""):
        if not auth.use_launch_token(t):
            return RedirectResponse("/login", 303)
        if auth.needs_setup:
            return RedirectResponse("/setup", 303)
        users = engine.storage.query("SELECT username FROM users ORDER BY created LIMIT 1")
        resp = RedirectResponse("/", 303)
        resp.set_cookie(COOKIE, auth.new_session(users[0]["username"]), httponly=True, samesite="strict")
        return resp

    # ------------------------------------------------------------------ auth API
    @app.get("/api/auth/status")
    def auth_status(req: Request):
        return {"needs_setup": auth.needs_setup, "user": current_user(req), "app": APP_NAME, "version": __version__,
                "local": client_ip(req) in LOCAL}

    @app.post("/api/setup")
    async def do_setup(req: Request):
        body = await req.json()
        err = auth.create_first_user(body.get("username", ""), body.get("password", ""), body.get("setup_code"),
                                     client_ip(req) in LOCAL)
        if err:
            return jr({"ok": False, "error": err}, 400)
        engine.bus.emit("SYSTEM", "SYSTEM", f"dashboard user '{body.get('username')}' created")
        tok, _ = auth.login(body["username"], body["password"], client_ip(req))
        resp = jr({"ok": True})
        resp.set_cookie(COOKIE, tok, httponly=True, samesite="strict", secure=req.url.scheme == "https")
        return resp

    @app.post("/api/login")
    async def do_login(req: Request):
        body = await req.json()
        tok, msg = auth.login(body.get("username", ""), body.get("password", ""), client_ip(req))
        if not tok:
            engine.bus.emit("SYSTEM", "SYSTEM", f"failed dashboard login from {client_ip(req)}")
            return jr({"ok": False, "error": msg}, 429 if msg.startswith("too many") else 401)
        resp = jr({"ok": True})
        resp.set_cookie(COOKIE, tok, httponly=True, samesite="strict", secure=req.url.scheme == "https",
                        max_age=int(auth.ttl))
        return resp

    @app.post("/api/logout")
    def do_logout(req: Request):
        auth.logout(req.cookies.get(COOKIE))
        resp = jr({"ok": True})
        resp.delete_cookie(COOKIE)
        return resp

    # ------------------------------------------------------------------ data API
    @app.get("/api/state")
    def state(req: Request):
        require(req)
        return jr(engine.state_cache or {"status": "starting"})

    @app.get("/api/chart")
    def chart(req: Request, symbol: str, tf: str = "M5", limit: int = 400):
        require(req)
        if symbol not in engine.symbols or tf not in ("M1", "M5", "M15", "H1", "H4", "D1"):
            raise HTTPException(400, "bad symbol/timeframe")
        return jr(build_chart(engine, symbol, tf, max(50, min(limit, 1500))))

    @app.get("/api/events")
    def events(req: Request, n: int = 200, bot: str | None = None):
        require(req)
        return jr(engine.bus.recent(min(n, 1000), bot))

    @app.get("/api/bot/{name}")
    def bot(req: Request, name: str):
        require(req)
        for b in engine.bots:
            if b.name.lower().replace(" ", "") == name.lower().replace(" ", ""):
                return jr({**b.snapshot(), "log": engine.bus.recent(150, b.name)})
        raise HTTPException(404, "unknown bot")

    @app.get("/api/trades")
    def trades(req: Request, status: str | None = None, limit: int = 200):
        require(req)
        return jr(engine.storage.trades(status=status, limit=min(limit, 2000)))

    @app.get("/api/equity")
    def equity(req: Request, account: str | None = None, hours: float = 24 * 30):
        require(req)
        return jr(engine.storage.equity_history(account, time.time() - hours * 3600))

    @app.get("/api/analyst")
    def analyst(req: Request):
        require(req)
        return jr(engine.analyst.report)

    @app.get("/api/lan")
    def lan(req: Request):
        require(req)
        port = cfg.get("dashboard.port")
        url = f"http://{lan_ip()}:{port}/"
        return {"url": url, "qr": "data:image/png;base64," + base64.b64encode(qr_png(url)).decode()}

    # ------------------------------------------------------------------ controls (POST, login + CSRF header)
    @app.post("/api/control/pause")
    async def ctl_pause(req: Request):
        u = require_control(req)
        body = await req.json()
        acc, paused = body.get("account", "*"), bool(body.get("paused", True))
        ok = engine.pause(acc, paused)
        engine.bus.emit("SYSTEM", "SYSTEM", f"{'PAUSE' if paused else 'RESUME'} {acc} by {u}")
        return {"ok": ok}

    @app.post("/api/control/kill")
    async def ctl_kill(req: Request):
        u = require_control(req)
        body = await req.json()
        engine.kill(bool(body.get("on", True)), by=u)
        return {"ok": True, "kill_switch": engine.guardian.kill_switch}

    @app.post("/api/strategy/apply")
    async def strat_apply(req: Request):
        u = require_control(req)
        body = await req.json()
        return jr(engine.strategist.apply(int(body["id"]), by=f"user {u}"))

    @app.post("/api/strategy/reject")
    async def strat_reject(req: Request):
        require_control(req)
        body = await req.json()
        return jr(engine.strategist.reject(int(body["id"])))

    @app.post("/api/strategy/lock")
    async def strat_lock(req: Request):
        require_control(req)
        body = await req.json()
        engine.strategist.set_lock(body["symbol"], bool(body.get("locked", True)))
        return {"ok": True}

    # ------------------------------------------------------------------ TradingView webhook (extra signal input)
    @app.post("/api/webhook/tradingview")
    async def tv_webhook(req: Request):
        ip = client_ip(req)
        if tv_limiter.blocked(ip) > 0:
            raise HTTPException(429, "rate limited")
        tv_limiter.hit(ip)
        if not tv_secret:
            raise HTTPException(404, "webhook disabled (set TRADINGVIEW_WEBHOOK_SECRET)")
        try:
            body = json.loads((await req.body()).decode("utf-8"))
        except ValueError:
            raise HTTPException(400, "JSON body required")
        import hmac
        if not hmac.compare_digest(str(body.get("secret", "")), tv_secret):
            raise HTTPException(403, "bad secret")
        sym = str(body.get("symbol", "")).upper().replace("OANDA:", "").replace("FX:", "")
        direction = str(body.get("direction", body.get("side", ""))).lower()
        direction = {"buy": "long", "sell": "short"}.get(direction, direction)
        if sym not in engine.symbols or direction not in ("long", "short"):
            raise HTTPException(400, "symbol/direction invalid")
        if engine.intel is not None:
            engine.intel.add_tv_alert(sym, direction, time.time(), str(body.get("message", ""))[:120])
        engine.bus.emit("SIGNAL", "SIGNAL", f"TradingView alert {sym} {direction}: {str(body.get('message', ''))[:80]} "
                        "(extra input: small bounded score note only, never a trade by itself)", sym)
        return {"ok": True}

    # ------------------------------------------------------------------ WebSocket
    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        tok = sock.cookies.get(COOKIE)
        user = auth.user_for(tok) or ("api-token" if auth.check_api_token(sock.query_params.get("token")) else None)
        if not user:
            await sock.close(code=4401)
            return
        await sock.accept()
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        loop = asyncio.get_running_loop()

        def on_event(ev):
            try:
                loop.call_soon_threadsafe(q.put_nowait, ev)
            except Exception:
                pass
        engine.bus.subscribe(on_event)
        period = float(cfg.get("dashboard.refresh_seconds", 1.0))
        try:
            await sock.send_text(_json({"type": "events", "events": engine.bus.recent(150)}))
            last = 0.0
            while True:
                now = time.time()
                if now - last >= period:
                    last = now
                    await sock.send_text(_json({"type": "state", "state": engine.state_cache}))
                evs = []
                try:
                    while True:
                        evs.append(q.get_nowait())
                except asyncio.QueueEmpty:
                    pass
                if evs:
                    await sock.send_text(_json({"type": "events", "events": evs}))
                await asyncio.sleep(0.25)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            if on_event in engine.bus.listeners:
                engine.bus.listeners.remove(on_event)

    return app


# ------------------------------------------------------------------ serve
def build_engine(cfg, simulate: bool = False, simulate_speed: float = 10.0):
    from aurum_nexus.data.fallback_adapter import FallbackPublicAdapter
    from aurum_nexus.data.feed import PriceFeed
    from aurum_nexus.data.oanda_adapter import OandaAdapter
    from aurum_nexus.engine import Engine
    from aurum_nexus.intel.news import NewsCalendar
    from aurum_nexus.intel.sources import IntelHub
    if simulate:
        from aurum_nexus.data.mock_adapter import MockAdapter
        syms = list(dict.fromkeys(cfg.symbols + ["EURUSD", "USDJPY", "GBPUSD", "USDCAD", "USDSEK", "USDCHF"]))
        mock = MockAdapter(symbols=syms, minutes=8 * 1440, start_ts=int(time.time()) - 9 * 86400)
        base_now, t0 = mock.now, time.time()

        def sim_clock():
            mock.now = base_now + (time.time() - t0) * simulate_speed
            return mock.now
        feed = PriceFeed(cfg, simulated=mock, clock=sim_clock)
        eng = Engine(cfg, feed, clock=sim_clock, news=None, intel=None, adapter_factory=lambda acc: None)
        eng.bus.emit("SYSTEM", "SYSTEM", "*** SIMULATE MODE: all prices are SIMULATED, not market data ***")
        return eng
    oanda = OandaAdapter()
    if oanda.configured:
        oanda.connect()
        if oanda.connected:
            oanda.start_stream(cfg.symbols)
    fallback = FallbackPublicAdapter(day_boundary=cfg.get("system.day_boundary"))
    feed = PriceFeed(cfg, oanda=oanda if oanda.configured else None, fallback=fallback)
    news = NewsCalendar(cfg, cache_dir=cfg.data_dir)
    intel = IntelHub(cfg, oanda=oanda if oanda.configured else None)
    return Engine(cfg, feed, news=news, intel=intel)


class Hub:
    """A running hub: engine thread + uvicorn server thread. Used by `mt5sys run` and the desktop app."""

    def __init__(self, cfg, simulate: bool = False, simulate_speed: float = 10.0):  # pragma: no cover - runtime
        import uvicorn
        self.cfg = cfg
        self.engine = build_engine(cfg, simulate, simulate_speed)
        self.app = create_app(self.engine, cfg)
        self.auth: Auth = self.app.state.auth
        self.port = int(cfg.get("dashboard.port"))
        self.url_local = f"http://127.0.0.1:{self.port}/"
        self.url_lan = f"http://{lan_ip()}:{self.port}/"
        self._stop = threading.Event()
        self.engine_thread = threading.Thread(target=self.engine.run_forever, args=(self._stop,), daemon=True,
                                              name="engine")
        self.server = uvicorn.Server(uvicorn.Config(self.app, host=cfg.get("dashboard.host"), port=self.port,
                                                    log_level="warning"))
        self.server_thread = threading.Thread(target=self.server.run, daemon=True, name="uvicorn")

    def start(self) -> "Hub":
        self.engine_thread.start()
        self.server_thread.start()
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.1)
        return self

    def launch_url(self) -> str:
        return self.url_local + "auth/launch?t=" + self.auth.make_launch_token()

    def banner(self, simulate: bool = False) -> None:
        print(f"\n  {APP_NAME} v{__version__} - mode {self.cfg.mode.upper()}{' (SIMULATED DATA)' if simulate else ''}")
        print(f"  Dashboard (this PC): {self.url_local}")
        print(f"  Dashboard (LAN/phone): {self.url_lan}")
        if self.auth.needs_setup:
            print(f"  FIRST RUN: create the dashboard user. Setup code for other devices: {self.auth.setup_code}")
        try:
            import qrcode
            qr = qrcode.QRCode(border=1)
            qr.add_data(self.url_lan)
            qr.print_ascii(invert=True)
        except Exception:
            pass

    def stop(self) -> None:
        self.server.should_exit = True
        self._stop.set()
        self.engine_thread.join(timeout=10)
        self.server_thread.join(timeout=10)
        self.engine.shutdown()


def serve(cfg, simulate: bool = False, open_browser: bool = True, simulate_speed: float = 10.0) -> int:  # pragma: no cover
    hub = Hub(cfg, simulate, simulate_speed).start()
    hub.banner(simulate)
    if open_browser:
        webbrowser.open(hub.launch_url())
    try:
        while hub.server_thread.is_alive():
            hub.server_thread.join(timeout=1.0)
    except KeyboardInterrupt:
        print("\n  stopping (state is persisted)...")
    finally:
        hub.stop()
    return 0
