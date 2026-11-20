"""Engine - one trading cycle across all bots. Used identically by the live hub and the backtester.

cycle(): accounts sync -> Scanner -> Levels (+DXY) -> live/paper gating -> Trade Manager on open trades
-> pending orders -> Signal -> Coordinator (Guardian / Risk / Execution) -> shadows -> Analyst/Strategist.
A snapshot for the dashboard is built at the end of each cycle (the API never blocks on the engine).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from aurum_nexus import APP_NAME, __version__
from aurum_nexus.bots.accounts import AccountHandle, AccountsManager
from aurum_nexus.bots.analyst import Analyst
from aurum_nexus.bots.execution import Execution
from aurum_nexus.bots.guardian import Guardian
from aurum_nexus.bots.levels import LevelsAgent
from aurum_nexus.bots.risk import RiskManager
from aurum_nexus.bots.scanner import Scanner
from aurum_nexus.bots.signal import SignalBot
from aurum_nexus.bots.strategist import Strategist
from aurum_nexus.bots.trade_manager import TradeManager
from aurum_nexus.coordinator import Coordinator
from aurum_nexus.data.feed import PriceFeed
from aurum_nexus.data.specs import generic_spec, make_converter
from aurum_nexus.intel.news import NewsCalendar
from aurum_nexus.intel.sources import IntelHub
from aurum_nexus.logbus import LogBus
from aurum_nexus.paper import PaperBroker
from aurum_nexus.storage import Storage
from aurum_nexus.timeutil import fmt_utc
from aurum_nexus.tradebook import TradeBook

log = logging.getLogger("aurum.engine")


class Engine:
    def __init__(self, cfg, feed: PriceFeed, storage: Storage | None = None, clock: Callable[[], float] | None = None,
                 news: NewsCalendar | None = None, intel: IntelHub | None = None, sleep=time.sleep,
                 adapter_factory: Callable | None = None, background: bool = True, persist_events: bool = True):
        self.cfg = cfg
        self.clock = clock or time.time
        self.feed = feed
        self.storage = storage or Storage(cfg.data_dir / "aurum_nexus.db")
        self.bus = LogBus(self.storage, clock=self.clock)
        self.bus.persist = persist_events
        self.book = TradeBook(self.storage, cfg.get("system.day_boundary"))
        self.news = news
        self.intel = intel
        self.background = background
        self.symbols = cfg.symbols
        self.started = self.clock()
        self.cycles = 0
        self.last_cycle_ms = 0.0
        self.lock = threading.RLock()
        self.state_cache: dict = {}
        self._bg_last: dict[str, float] = {}
        self._stop = threading.Event()
        # bots
        self.strategist = Strategist(self.bus, cfg, self.storage, clock=self.clock)
        self.strategist.load(self.symbols)
        self.scanner = Scanner(self.bus, cfg, feed, clock=self.clock)
        self.levels = LevelsAgent(self.bus, cfg, feed, clock=self.clock)
        self.analyst = Analyst(self.bus, cfg, self.book, self.storage, clock=self.clock)
        self.signal = SignalBot(self.bus, cfg, self.levels, self.strategist, self.analyst, intel, clock=self.clock)
        self.risk = RiskManager(self.bus, cfg, clock=self.clock)
        self.tm = TradeManager(self.risk, cfg, self.levels, news, self.storage)
        self.guardian = Guardian(self.bus, cfg, self.book, news, self.risk, clock=self.clock)
        self.execution = Execution(self.bus, cfg, self.book, self.storage, self.tm, sleep=sleep, clock=self.clock)
        self.accounts = AccountsManager(self.bus, cfg, self.storage, clock=self.clock)
        self.coordinator = Coordinator(cfg, self.bus, self.storage, self.accounts, self.risk, self.guardian,
                                       self.execution, self.strategist, feed, lambda: self.scanner.contexts)
        self.execution.pending_guard = self._pending_guard
        self.bots = [self.scanner, self.levels, self.signal, self.risk, self.execution, self.guardian, self.accounts,
                     self.analyst, self.strategist]
        self._build_accounts(adapter_factory)
        self.bus.emit("SYSTEM", "SYSTEM", f"{APP_NAME} v{__version__} started · mode {cfg.mode.upper()} · "
                      f"price source {cfg.get('system.price_source')} · symbols {', '.join(self.symbols)}")
        for w in cfg.warnings:
            self.bus.emit("SYSTEM", "SYSTEM", f"config warning: {w}")

    # ------------------------------------------------------------------ accounts
    def _paper_price_fn(self, h_ref: dict):
        def price(symbol: str):
            h = h_ref["h"]
            if h.adapter is not None and h.adapter.connected:
                try:
                    t = h.adapter.tick(symbol)
                    if t is not None:
                        return t
                except Exception:
                    pass
            return self.feed.tick(symbol)
        return price

    def _paper_spec_fn(self, h_ref: dict):
        cache: dict = {}

        def spec(symbol: str):
            h = h_ref["h"]
            if h.adapter is not None and h.adapter.connected:
                try:
                    s = h.adapter.symbol_spec(symbol)
                    if s is not None:
                        cache[symbol] = s
                        return s
                except Exception:
                    pass
            conv = make_converter(lambda s: (self.feed.tick(s).mid if self.feed.tick(s) else None))
            return generic_spec(symbol, h.cfg.currency, conv)
        return spec

    def _build_accounts(self, adapter_factory) -> None:
        from aurum_nexus.hub.worker import WorkerProxy
        for acc in self.cfg.accounts:
            adapter = None
            if acc.kind == "mt5" and acc.enabled:
                if adapter_factory is not None:
                    adapter = adapter_factory(acc)
                else:
                    adapter = WorkerProxy(acc, self.cfg.get("symbols.suffix_candidates"),
                                          self.cfg.get("system.server_utc_offset_hours"),
                                          int(acc.magic or self.cfg.get("execution.magic")))
            ref: dict = {}
            paper = PaperBroker(acc.name, acc.starting_balance or self.cfg.get("system.paper_starting_balance"),
                                acc.currency or self.cfg.get("system.paper_currency"), self._paper_price_fn(ref),
                                self._paper_spec_fn(ref), clock=self.clock,
                                magic=int(acc.magic or self.cfg.get("execution.magic")))
            h = AccountHandle(acc, adapter, paper)
            ref["h"] = h
            # restore persisted paper positions and balance
            open_paper = self.book.open_for(acc.name)
            paper.restore([t for t in open_paper if t.get("mode") == "paper"])
            closed_paper = [t for t in self.book.closed(acc.name) if t.get("mode") == "paper"]
            paper.balance += sum(float(t.get("pnl") or 0) for t in closed_paper)
            paper.balance += sum(float(t.get("pnl") or 0) for t in open_paper if t.get("mode") == "paper")
            self.accounts.add(h)
        if self.feed.mt5_sources is not None:
            self.feed.mt5_sources = lambda: [h.adapter for h in self.accounts.handles.values()
                                             if h.adapter is not None and getattr(h.adapter, "kind", "") == "mt5"]

    def _pending_guard(self, p: dict) -> str | None:
        """Re-check a pending/deferred order at fill time (state may have changed since it was queued)."""
        plan, h = p["plan"], p["handle"]
        if self.guardian.kill_switch:
            return "KILL SWITCH active"
        if h.name in self.guardian.lockouts:
            return self.guardian.lockouts[h.name]
        for t in self.book.open_for(h.name):
            if t["symbol"] == plan["symbol"]:
                return f"position already open on {plan['symbol']}"
        lim = (h.cfg.limits or {}).get("max_concurrent_positions", self.cfg.get("guardian.max_concurrent_positions"))
        if len(self.book.open_for(h.name)) >= int(lim):
            return "max concurrent positions"
        ev = self.news.blackout(plan["symbol"], self.clock()) if self.news is not None else None
        if ev:
            return f"news blackout: {ev['title']}"
        return None

    def start(self) -> None:
        self.accounts.connect_all()
        if self.news is not None and self.background:
            threading.Thread(target=self.news.refresh, daemon=True, name="news").start()

    # ------------------------------------------------------------------ background refreshes
    def _bg(self, key: str, every: float, fn) -> None:
        now = time.time()
        if now - self._bg_last.get(key, 0) < every:
            return
        self._bg_last[key] = now
        if self.background:
            threading.Thread(target=self._safe, args=(fn,), daemon=True, name=f"bg-{key}").start()
        else:
            self._safe(fn)

    @staticmethod
    def _safe(fn) -> None:
        try:
            fn()
        except Exception as e:  # pragma: no cover
            log.warning("background task failed: %s", e)

    # ------------------------------------------------------------------ cycle
    def cycle(self) -> dict:
        t0 = time.perf_counter()
        with self.lock:
            now = self.clock()
            self.cycles += 1
            self.accounts.sync(now)
            contexts = {}
            for s in self.symbols:
                try:
                    ctx = self.scanner.update(s)
                    self.levels.update(ctx)
                    contexts[s] = ctx
                except Exception as e:
                    self.bus.emit("ERROR", "SCANNER", f"{s} cycle error: {e}", s)
            dxy_state = self.levels.dxy.update(contexts)
            preview = any(c.preview for c in contexts.values()) if contexts else True
            for h in self.accounts.handles.values():
                self.accounts.evaluate_live(h, preview)
            self.strategist.live_mode = any(h.live for h in self.accounts.handles.values())
            self._manage(contexts, dxy_state, now)
            self.execution.process_pending(contexts, now)
            cands = []
            for s, ctx in contexts.items():
                try:
                    cands.extend(self.signal.evaluate(ctx, dxy_state, self.book.active_zone_ids(s)))
                except Exception as e:
                    self.bus.emit("ERROR", "SIGNAL", f"{s} signal error: {e}", s)
            self.coordinator.corr_live = {}
            if cands:
                self.coordinator.select(cands, now)
            prices = {s: (c.tick.bid, c.tick.ask) for s, c in contexts.items() if c.tick}
            self.tm.update_shadows(prices, now)
            self.analyst.refresh(now, contexts_symbols=self.symbols)
            if self.intel is not None:
                self._bg("intel", 300, lambda: self.intel.refresh(self.symbols))
                self._dom(contexts)
            if self.news is not None:
                self._bg("news", 900, self.news.refresh)
            self._learn(now)
            self.last_cycle_ms = (time.perf_counter() - t0) * 1000
            self.state_cache = self._build_state(contexts, dxy_state, now)
            return {"cycle": self.cycles, "candidates": len(cands), "ms": self.last_cycle_ms}

    def _dom(self, contexts) -> None:
        src = self.feed._mt5()
        if src is None:
            return
        for s, ctx in contexts.items():
            try:
                self.intel.update_dom(s, src.market_book(s), ctx.price, ctx.atr("M5"))
            except Exception:
                pass

    def _learn(self, now: float) -> None:
        if now - self._bg_last.get("learn", 0) < 3600:
            return
        self._bg_last["learn"] = now
        for s in self.symbols:
            by_setup = {}
            for t in self.book.closed():
                if t["symbol"] == s and t.get("r_multiple") is not None:
                    by_setup.setdefault(t["setup"], []).append(t)
            from aurum_nexus.bots.analyst import stats
            st = {k: stats(v) for k, v in by_setup.items()}
            if st:
                self.strategist.learn(s, st)

    def _manage(self, contexts, dxy_state, now: float) -> None:
        for t in list(self.book.open.values()):
            h = self.accounts.handles.get(t["account"])
            if h is None:
                continue
            if t["mode"] == "live":
                if h.adapter is None or not h.adapter.connected:
                    continue
                if not any(p.ticket == t["ticket"] for p in h.positions) and h.last_ok >= now - 2:
                    self._reconcile_live_close(t, h, now)
                    continue
                trader = h.adapter
            else:
                trader = h.paper
            tick = trader.tick(t["symbol"])
            if tick is None:
                continue
            ctx = contexts.get(t["symbol"])
            acts = self.tm.evaluate(t, ctx, tick.bid, tick.ask, now, dxy_state)
            for a in acts:
                if a["action"] == "close":
                    ok = self.execution.close(t, h, a["fraction"], a["reason"], a.get("exit_type", "early"), now,
                                              price_hint=a.get("price") if t["mode"] == "paper" else None,
                                              rule=a.get("rule"))
                    if ok and a.get("mark"):
                        t[a["mark"]] = 1
                        if t["status"] == "open":
                            self.book.save(t)
                    if t["status"] != "open":
                        break
                elif a["action"] == "modify_sl":
                    if self.execution.modify_sl(t, h, a["sl"], a["reason"]) and a.get("mark"):
                        t[a["mark"]] = 1
                        self.book.save(t)
            if t["status"] == "open" and t.get("tp2_done") and t["mode"] == "live" and t.get("broker_tp"):
                # runner: remove the failsafe TP, the trail manages the exit
                t["broker_tp"] = 0.0
                self.execution.modify_sl(t, h, t["stop"], "runner: TP removed, trailing by M5 structure")

    def _reconcile_live_close(self, t: dict, h, now: float) -> None:
        deals = [d for d in h.deals_today if d.position_id == t["ticket"] and d.entry in ("out", "out_by", "inout")]
        sign = 1 if t["direction"] == "long" else -1
        if deals:
            px = deals[-1].price
            init = abs(t["entry"] - t["initial_stop"]) or 1e-12
            vol = sum(d.volume for d in deals)
            frac = min(1.0, vol / float(t["volume"]))
            t["realized_r"] = float(t.get("realized_r") or 0) + (px - t["entry"]) * sign / init * frac
            t["pnl"] = float(t.get("pnl") or 0) + sum(d.net for d in deals)
            t["exit_price"] = px
            et = "sl" if abs(px - t["stop"]) <= abs(px - t.get("broker_tp", t["tp2"])) else "tp"
        else:
            et = "external"
        t["status"], t["close_ts"], t["volume_open"] = "closed", now, 0.0
        t["r_multiple"] = round(float(t.get("realized_r") or 0), 3)
        t["exit_type"] = et
        t["exit_reason"] = "closed at broker (SL/TP/manual)"
        self.book.close(t)
        self.bus.emit("CLOSE", "EXECUTION", f"CLOSE {t['symbol']} {t['r_multiple']:+.2f}R: {t['exit_reason']} "
                      f"({h.name})", t["symbol"])

    # ------------------------------------------------------------------ run loop
    def run_forever(self, stop: threading.Event | None = None) -> None:
        stop = stop or self._stop
        self.start()
        period = float(self.cfg.get("system.cycle_seconds", 1.0))
        while not stop.is_set():
            t0 = time.time()
            try:
                self.cycle()
            except Exception as e:  # never die silently
                log.exception("cycle failed")
                self.bus.emit("ERROR", "SYSTEM", f"cycle failed: {e}")
            stop.wait(max(0.05, period - (time.time() - t0)))

    def stop(self) -> None:
        self._stop.set()

    def shutdown(self) -> None:
        self._stop.set()
        with self.lock:
            for t in self.book.open.values():
                self.book.save(t)
            for h in self.accounts.handles.values():
                if h.adapter is not None and hasattr(h.adapter, "stop"):
                    try:
                        h.adapter.stop()
                    except Exception:
                        pass
            self.storage.kv_set("last_shutdown", self.clock())
            self.bus.emit("SYSTEM", "SYSTEM", "shutdown: state persisted")
            self.storage.close()

    # ------------------------------------------------------------------ controls
    def kill(self, on: bool = True, by: str = "user") -> None:
        self.guardian.set_kill(on, by)
        if on:
            n = self.execution.cancel_pending()
            if n:
                self.bus.emit("BLOCKED", "EXECUTION", f"kill switch: {n} pending order(s) cancelled")

    def pause(self, account: str | None, paused: bool) -> bool:
        if account in (None, "*", "all"):
            self.accounts.pause_all(paused)
            return True
        return self.accounts.pause(account, paused)

    # ------------------------------------------------------------------ dashboard state
    def _build_state(self, contexts, dxy_state, now: float) -> dict:
        handles = list(self.accounts.handles.values())
        trade_r = {(t["account"], t["ticket"]): round(t.get("r_now", 0.0), 2) for t in self.book.open.values()}
        acc_views = [self.accounts.view(h, now, trade_r) for h in handles]
        # totals per currency (never mix currencies)
        totals: dict[str, dict] = {}
        for v in acc_views:
            r = v["real"]
            if r and r.get("status") in ("OK", "STALE") and r.get("currency"):
                cur = r["currency"]
                tt = totals.setdefault(cur, {"balance": 0.0, "equity": 0.0, "profit": 0.0, "realized_today": 0.0,
                                             "accounts": 0})
                tt["balance"] += r.get("balance") or 0
                tt["equity"] += r.get("equity") or 0
                tt["profit"] += r.get("profit") or 0
                tt["realized_today"] += r.get("realized_today") or 0
                tt["accounts"] += 1
        paper_totals: dict[str, dict] = {}
        for v in acc_views:
            p = v["paper"]
            if p:
                tt = paper_totals.setdefault(p["currency"], {"balance": 0.0, "equity": 0.0, "profit": 0.0,
                                                             "start_balance": 0.0, "accounts": 0})
                tt["balance"] += p["balance"] or 0
                tt["equity"] += p["equity"] or 0
                tt["profit"] += p["profit"] or 0
                tt["start_balance"] += p.get("start_balance") or 0
                tt["accounts"] += 1
        open_trades = []
        for t in self.book.open.values():
            ctx = contexts.get(t["symbol"])
            eta = self.tm.eta(t, ctx, t.get("last_price") or t["entry"], now) if ctx else {}
            open_trades.append({**{k: t.get(k) for k in ("id", "account", "mode", "symbol", "direction", "setup",
                                                          "timeframe", "entry", "stop", "tp1", "tp2", "volume",
                                                          "volume_open", "open_ts", "score", "mfe_r", "mae_r",
                                                          "r_now", "tp1_done", "tp2_done", "t1_deadline",
                                                          "t2_deadline", "risk_money", "ticket")}, "eta": eta})
        quotes = {s: self.feed.quote_status(s) for s in self.symbols}
        main = quotes.get(self.symbols[0], {}) if self.symbols else {}
        stats = self.analyst.report.get("overall", {})
        return {
            "app": APP_NAME, "version": __version__, "now": now, "now_text": fmt_utc(now, True),
            "uptime": now - self.started, "mode": self.cfg.mode, "cycles": self.cycles,
            "cycle_ms": round(self.last_cycle_ms, 1), "kill_switch": self.guardian.kill_switch,
            "symbols": self.symbols, "quotes": quotes, "source": {"kind": main.get("source"), "label": main.get("label"),
                                                                  "preview": main.get("preview"), "state": main.get("state")},
            "sources": self.feed.sources_status(),
            "accounts": acc_views, "totals": totals, "paper_totals": paper_totals,
            "accounts_connected": sum(1 for h in handles if h.connected),
            "accounts_mt5": sum(1 for h in handles if h.is_mt5 and h.cfg.enabled),
            "open_trades": open_trades, "stats": stats,
            "bots": [b.snapshot() for b in self.bots],
            "ranking": list(self.coordinator.ranking)[:25],
            "slots": {h.name: {"open": len(self.book.open_for(h.name)),
                               "max": int((h.cfg.limits or {}).get("max_concurrent_positions",
                                                                   self.cfg.get("guardian.max_concurrent_positions"))),
                               "open_risk_pct": round(self.guardian.open_risk_pct(h.name, h.equity() or 0), 2)}
                      for h in handles},
            "levels": {s: getattr(c, "levels_top", []) for s, c in contexts.items()},
            "dxy": self.levels.dxy.snapshot(160),
            "regime": {s: c.regime for s, c in contexts.items()},
            "context": {s: {"session": c.session, "adr": c.adr, "adr_used": c.adr_used, "atr_regime": c.atr_regime,
                            "has_volume": c.has_volume, "htf_bias": c.htf_bias, "price": c.price,
                            "spread": c.spread, "daily": c.daily, "asia": c.asia, "preview": c.preview}
                        for s, c in contexts.items()},
            "trade_manager": {"rules": self.cfg.get("trade_manager.rules"), "fired": self.tm.fired,
                              "time_targets": self.cfg.get("time_targets"),
                              "shadows_open": list(self.tm.shadows.values())[:10]},
            "strategy": {s: self.strategist.summary(s, (contexts[s].regime or {}).get("regime") if s in contexts else None)
                         for s in self.symbols},
            "intel": self.intel.snapshot() if self.intel else {},
            "news": self.news.snapshot(now) if self.news else {"status": "disabled"},
            "analyst": {"warnings": self.analyst.warnings, "suggestions": self.analyst.suggestions,
                        "frequency": self.analyst.report.get("frequency", {}),
                        "adaptive": self.analyst.report.get("adaptive", {})},
            "pending_orders": [{"symbol": p["plan"]["symbol"], "direction": p["plan"]["direction"], "kind": p["kind"],
                                "entry": p["plan"]["entry"], "account": p["handle"].name, "expires": p["expires"]}
                               for p in self.execution.pending],
            "guardian": {"lockouts": self.guardian.lockouts, "vetoes": self.guardian.vetoes[-10:]},
            "warnings": self.cfg.warnings,
        }
