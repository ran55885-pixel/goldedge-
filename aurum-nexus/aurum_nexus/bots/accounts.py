"""ACCOUNTS - account manager: connection health, reconnect with backoff, balance/equity sync,
per-account pause/resume, live/paper gating per account.

Real MT5 figures (account_info, positions_get incl. manual/other EAs, history_deals_get since the broker
day start) are always the main numbers when a terminal is connected. Paper results are kept separately
and labelled PAPER. Stale (>6s) / disconnected / no-data states carry the last real value + timestamp.
"""
from __future__ import annotations

import time
from dataclasses import asdict

from aurum_nexus.bots.base import Bot
from aurum_nexus.config import effective_live
from aurum_nexus.models import AccountSnapshot
from aurum_nexus.paper import PaperBroker
from aurum_nexus.timeutil import broker_day_start


class AccountHandle:
    def __init__(self, cfg_acc, adapter=None, paper: PaperBroker | None = None):
        self.cfg = cfg_acc
        self.name = cfg_acc.name
        self.adapter = adapter            # WorkerProxy / Mt5Adapter / MockAdapter (real terminal) or None
        self.paper = paper
        self.paused = False
        self.live = False
        self.live_reason = "paper"
        self.snapshot: AccountSnapshot = AccountSnapshot(name=self.name, status="NO DATA")
        self.last_ok: float = 0.0
        self.positions: list = []
        self.deals_today: list = []
        self.realized_today: float | None = None
        self.backoff = 1.0
        self.next_retry = 0.0
        self.attempts = 0
        self.peak_equity: float | None = None
        self.error = ""

    @property
    def is_mt5(self) -> bool:
        return self.cfg.kind == "mt5"

    @property
    def connected(self) -> bool:
        return bool(self.adapter is not None and self.adapter.connected)

    @property
    def trader(self):
        """The back-end orders go to: the real terminal only when live, else the paper broker."""
        return self.adapter if self.live else self.paper

    @property
    def mode(self) -> str:
        return "live" if self.live else "paper"

    @property
    def currency(self) -> str:
        return self.snapshot.currency or self.cfg.currency

    def equity(self) -> float | None:
        if self.live:
            return self.snapshot.equity
        ps = self.paper.account() if self.paper else None
        return ps.equity if ps else None


class AccountsManager(Bot):
    name = "ACCOUNTS"
    color = "#00ffa3"
    job = "Connections, reconnects, balances, positions, pause/resume"

    def __init__(self, bus, cfg, storage, clock=None):
        super().__init__(bus, cfg, clock)
        self.storage = storage
        self.handles: dict[str, AccountHandle] = {}
        self.stale_after = float(cfg.get("system.stale_after_seconds", 6))
        self.day_boundary = cfg.get("system.day_boundary")
        self._last_equity_save: dict[str, float] = {}
        self.magic = int(cfg.get("execution.magic"))

    def add(self, handle: AccountHandle) -> None:
        self.handles[handle.name] = handle

    def config_view(self) -> dict:
        return {"accounts": [h.cfg.public_dict() for h in self.handles.values()]}

    # ------------------------------------------------------------------ connection
    def connect_all(self) -> None:
        for h in self.handles.values():
            if h.adapter is not None and h.cfg.enabled:
                self._try_connect(h, self.clock())

    def _try_connect(self, h: AccountHandle, now: float) -> bool:
        h.attempts += 1
        try:
            if hasattr(h.adapter, "start"):
                h.adapter.start()
            ok = bool(h.adapter.connect())
        except Exception as e:
            ok = False
            h.adapter.last_error = str(e)[:200]
        if ok:
            h.backoff = 1.0
            h.error = ""
            self.log("ACCOUNT", f"{h.name} connected ({h.cfg.server or 'n/a'})", data={"account": h.name})
        else:
            h.error = getattr(h.adapter, "last_error", "") or "connect failed"
            h.next_retry = now + h.backoff
            self.log("ACCOUNT", f"{h.name} connect failed: {h.error} - retry in {h.backoff:.0f}s",
                     data={"account": h.name})
            h.backoff = min(h.backoff * 2, 60.0)
        return ok

    def evaluate_live(self, h: AccountHandle, source_is_preview: bool) -> None:
        live, why = effective_live(self.cfg, h.cfg, h.snapshot.trade_mode if h.snapshot.status == "OK" else None,
                                   source_is_preview)
        if live and not h.connected:
            live, why = False, "terminal not connected"
        if live != h.live:
            self.log("ACCOUNT", f"{h.name} mode -> {'LIVE' if live else 'PAPER'} ({why})", data={"account": h.name})
        h.live, h.live_reason = live, why

    # ------------------------------------------------------------------ sync
    def sync(self, now: float | None = None) -> None:
        now = now if now is not None else self.clock()
        day_start = broker_day_start(now, self.day_boundary)
        ok_n = 0
        for h in self.handles.values():
            if not h.cfg.enabled:
                h.snapshot.status = "DISABLED"
                continue
            if h.adapter is not None:
                if not h.adapter.connected:
                    if now >= h.next_retry:
                        self._try_connect(h, now)
                    self._mark_stale(h, now, disconnected=True)
                else:
                    try:
                        snap = h.adapter.account()
                        if snap.status == "OK":
                            h.snapshot = snap
                            h.last_ok = now
                            h.positions = h.adapter.positions()
                            h.deals_today = h.adapter.deals(day_start, now + 60)
                            h.realized_today = sum(d.net for d in h.deals_today if d.type in ("buy", "sell"))
                            ok_n += 1
                            if h.snapshot.equity and (h.peak_equity is None or h.snapshot.equity > h.peak_equity):
                                h.peak_equity = h.snapshot.equity
                        else:
                            self._mark_stale(h, now, disconnected=True, err=snap.error)
                    except Exception as e:
                        h.error = str(e)[:200]
                        self._mark_stale(h, now, disconnected=True, err=h.error)
            if h.paused and h.snapshot.status == "OK":
                h.snapshot.status = "OK"  # paused is shown as a separate flag
            self._save_equity(h, now)
        n_mt5 = sum(1 for h in self.handles.values() if h.is_mt5 and h.cfg.enabled)
        self.set_status(f"{ok_n}/{n_mt5} MT5 connected · {sum(1 for h in self.handles.values() if h.paused)} paused")
        self.pulse(ok_n)

    def _mark_stale(self, h: AccountHandle, now: float, disconnected: bool = False, err: str = "") -> None:
        if disconnected:
            h.snapshot.status = "DISCONNECTED"
        elif h.last_ok and now - h.last_ok > self.stale_after:
            h.snapshot.status = "STALE"
        if err:
            h.snapshot.error = err

    def _save_equity(self, h: AccountHandle, now: float) -> None:
        if now - self._last_equity_save.get(h.name, 0) < 10:
            return
        self._last_equity_save[h.name] = now
        if h.adapter is not None and h.snapshot.status == "OK":
            self.storage.save_equity(h.name, h.snapshot.currency, h.snapshot.balance, h.snapshot.equity, "mt5", now)
        if h.paper is not None:
            ps = h.paper.account()
            self.storage.save_equity(f"{h.name}#paper", ps.currency, ps.balance, ps.equity, "paper", now)

    # ------------------------------------------------------------------ controls
    def pause(self, name: str, paused: bool = True) -> bool:
        h = self.handles.get(name)
        if not h:
            return False
        h.paused = paused
        self.log("ACCOUNT", f"{name} {'PAUSED' if paused else 'RESUMED'}", data={"account": name})
        return True

    def pause_all(self, paused: bool = True) -> None:
        for n in self.handles:
            self.pause(n, paused)

    # ------------------------------------------------------------------ views
    def view(self, h: AccountHandle, now: float, trade_risk: dict | None = None) -> dict:
        s = h.snapshot
        age = now - h.last_ok if h.last_ok else None
        status = s.status
        if status == "OK" and age is not None and age > self.stale_after:
            status = "STALE"
        real = None
        if h.adapter is not None:
            real = {**asdict(s), "status": status, "age": age, "last_ok": h.last_ok or None,
                    "realized_today": h.realized_today, "error": h.error or s.error,
                    "positions": [self._pos_view(p, trade_risk) for p in h.positions]}
        paper = None
        if h.paper is not None:
            ps = h.paper.account()
            paper_deals = h.paper.deals(broker_day_start(now, self.day_boundary), now + 60)
            paper = {**asdict(ps), "realized_today": sum(d.net for d in paper_deals if d.entry != "in"),
                     "positions": [self._pos_view(p, trade_risk) for p in h.paper.positions()],
                     "start_balance": h.paper.start_balance}
        return {"name": h.name, "kind": h.cfg.kind, "mode": h.mode, "live_reason": h.live_reason,
                "login_cfg": h.cfg.login or None, "server_cfg": h.cfg.server or None,
                "paused": h.paused, "enabled": h.cfg.enabled, "risk_pct": h.cfg.risk_pct,
                "symbols": h.cfg.symbols, "real": real, "paper": paper, "attempts": h.attempts,
                "next_retry": h.next_retry if not h.connected else None}

    @staticmethod
    def _pos_view(p, trade_risk: dict | None) -> dict:
        d = p.to_dict() if hasattr(p, "to_dict") else dict(p)
        key = (d.get("account"), d.get("ticket"))
        tr = (trade_risk or {}).get(key)
        if tr:
            d["r"] = tr
        elif d.get("sl"):
            risk = abs(d["price_open"] - d["sl"])
            move = (d["price_current"] - d["price_open"]) * (1 if d["side"] == "buy" else -1)
            d["r"] = round(move / risk, 2) if risk > 0 else None
        else:
            d["r"] = None   # shown as "—" without SL
        return d
