"""EXECUTION - order courier.

order_send with filling-mode detection (IOC / FOK / RETURN from symbol_info.filling_mode, switching on
"invalid filling"), magic number, deviation, retries on requote / price change / timeout / connection.
Price, spread and RR are re-checked immediately before sending and again after the fill (a fill whose
RR fell below the floor is closed at once). Market vs virtual-limit entries by setup type. Orders are
deferred (not dropped) during the first seconds of a new candle and during the rollover window.
Slippage is recorded for every fill.
"""
from __future__ import annotations

import math
import time

from aurum_nexus.bots.base import Bot
from aurum_nexus.bots.risk import rr_check, step_decimals
from aurum_nexus.data.base import RET_INVALID_FILL, RETCODE_TEXT, RETRYABLE
from aurum_nexus.logbus import fmt_r
from aurum_nexus.models import TF_SECONDS
from aurum_nexus.timeutil import in_window

FILL_CODE = {"FOK": 0, "IOC": 1, "RETURN": 2}


def filling_sequence(filling_mode_flags: int, preference: list[str]) -> list[int]:
    """Order of ORDER_FILLING_* codes to try. SYMBOL_FILLING_FOK=1, SYMBOL_FILLING_IOC=2 (bit flags);
    RETURN is tried last as a fallback."""
    allowed = {"FOK": bool(filling_mode_flags & 1), "IOC": bool(filling_mode_flags & 2), "RETURN": True}
    seq = [FILL_CODE[p] for p in preference if allowed.get(p)]
    for p in ("IOC", "FOK", "RETURN"):
        if FILL_CODE[p] not in seq:
            seq.append(FILL_CODE[p])
    return seq


class Execution(Bot):
    name = "EXECUTION"
    color = "#ff9f1c"
    job = "Order routing, filling modes, retries, slippage, virtual limits"

    def __init__(self, bus, cfg, book, storage, tm=None, sleep=time.sleep, clock=None):
        super().__init__(bus, cfg, clock)
        self.ec = cfg.get("execution")
        self.book = book
        self.storage = storage
        self.tm = tm
        self.sleep = sleep
        self.pending: list[dict] = []       # deferred market orders and virtual limits
        self.slippage: list[float] = []
        self.pending_guard = None           # callable(pending) -> veto reason | None (set by the engine)

    def config_view(self) -> dict:
        return dict(self.ec)

    # ------------------------------------------------------------------ deferral / limits
    def must_defer(self, now: float) -> str | None:
        if now % 60 < float(self.ec["avoid_first_seconds"]):
            return "first seconds of a new candle"
        ro = self.cfg.get("sessions.rollover")
        if ro and in_window(now, ro):
            return "rollover / swap window"
        return None

    def submit(self, plan: dict, handle, candidate, ctx, profile: dict, now: float) -> dict | None:
        if plan["entry_type"] == "limit":
            exp = now + int(self.ec["limit_validity_bars"]) * TF_SECONDS.get(candidate.timeframe, 300)
            self.pending.append({"kind": "limit", "plan": plan, "handle": handle, "candidate": candidate,
                                 "profile": profile, "expires": exp, "created": now})
            self.log("NEW TRADE", f"VIRTUAL LIMIT {plan['symbol']} {plan['direction']} @ {plan['entry']:.6g} "
                     f"({handle.name}, valid {int((exp - now) / 60)}m)", plan["symbol"])
            return None
        why = self.must_defer(now)
        if why:
            self.pending.append({"kind": "deferred", "plan": plan, "handle": handle, "candidate": candidate,
                                 "profile": profile, "expires": now + 90, "created": now})
            self.log("MANAGE", f"DEFER {plan['symbol']} {plan['direction']} ({why}) - will retry", plan["symbol"])
            return None
        return self.open_market(plan, handle, candidate, ctx, profile, now)

    def process_pending(self, contexts: dict, now: float) -> list[dict]:
        opened = []
        keep = []
        for p in self.pending:
            plan, h = p["plan"], p["handle"]
            if now > p["expires"]:
                self.log("SKIP", f"{p['kind']} order {plan['symbol']} {plan['direction']} expired ({h.name})",
                         plan["symbol"])
                continue
            if h.paused:
                keep.append(p)
                continue
            if self.pending_guard is not None:
                veto = self.pending_guard(p)
                if veto:
                    self.log("BLOCKED", f"{p['kind']} order {plan['symbol']} {plan['direction']} cancelled on "
                             f"{h.name}: {veto}", plan["symbol"])
                    continue
            if p["kind"] == "deferred":
                if self.must_defer(now):
                    keep.append(p)
                    continue
                t = self.open_market(plan, h, p["candidate"], contexts.get(plan["symbol"]), p["profile"], now)
                if t:
                    opened.append(t)
                continue
            tick = h.trader.tick(plan["symbol"]) if h.trader is not None else None
            if tick is None:
                keep.append(p)
                continue
            touched = (tick.ask <= plan["entry"]) if plan["direction"] == "long" else (tick.bid >= plan["entry"])
            if touched and not self.must_defer(now):
                t = self.open_market(plan, h, p["candidate"], contexts.get(plan["symbol"]), p["profile"], now,
                                     from_limit=True)
                if t:
                    opened.append(t)
            else:
                keep.append(p)
        self.pending = keep
        return opened

    def cancel_pending(self, symbol: str | None = None, account: str | None = None) -> int:
        before = len(self.pending)
        self.pending = [p for p in self.pending if not ((symbol is None or p["plan"]["symbol"] == symbol)
                                                         and (account is None or p["handle"].name == account))]
        return before - len(self.pending)

    # ------------------------------------------------------------------ open
    def _send(self, trader, spec, fn, *args):
        """Send with filling detection + retries. fn(filling) -> OrderResult."""
        seq = filling_sequence(spec.filling_mode if spec else 3, self.ec["filling_preference"])
        fi = 0
        attempts = 0
        res = None
        while attempts <= int(self.ec["max_retries"]):
            attempts += 1
            res = fn(seq[fi])
            res.attempts = attempts
            if res.ok:
                return res
            if res.retcode == RET_INVALID_FILL and fi + 1 < len(seq):
                fi += 1
                attempts -= 1
                continue
            if res.retcode in RETRYABLE:
                self.sleep(0.2)
                continue
            break
        return res

    def open_market(self, plan: dict, handle, candidate, ctx, profile: dict, now: float, from_limit=False) -> dict | None:
        trader = handle.trader
        sym = plan["symbol"]
        if trader is None:
            self.log("ERROR", f"{handle.name}: no trading back-end", sym)
            return None
        spec = trader.symbol_spec(sym)
        tick = trader.tick(sym)
        if spec is None or tick is None:
            self.log("SKIP", f"SKIPPED {sym} on {handle.name}: no symbol spec/quote", sym)
            return None
        # ---- re-check immediately before sending (price, spread, RR)
        entry = tick.ask if plan["direction"] == "long" else tick.bid
        ok, rr, why = rr_check(plan["direction"], entry, plan["stop"], plan["tp1"], plan["tp2"], plan["obstacle"],
                               self.cfg.min_rr)
        if not ok:
            self.log("SKIP", f"SKIPPED {sym} {plan['direction']} at send ({handle.name}): {why}", sym)
            self.storage.save_decision(plan["candidate_id"], handle.name, sym, "SKIPPED", f"pre-send {why}",
                                       candidate.score)
            return None
        atr5 = ctx.atr("M5") if ctx else None
        if atr5 and tick.spread > float(self.cfg.get("guardian.max_spread_atr_frac")) * atr5:
            self.log("SKIP", f"SKIPPED {sym} at send: spread {tick.spread:.5g} too wide", sym)
            return None
        side = plan["side"]
        magic = int(handle.cfg.magic or self.ec["magic"])
        dev = int(self.ec["deviation_points"])
        sign = 1 if plan["direction"] == "long" else -1
        broker_tp = plan["tp2"] + sign * (0.25 * atr5 if atr5 else 0.0)   # failsafe beyond TP2; TP1/TP2 managed
        comment = f"AN {candidate.setup[:12]} {int(candidate.score)}"
        res = self._send(trader, spec, lambda f: trader.market_order(sym, side, plan["lots"], plan["stop"], broker_tp,
                                                                   dev, magic, comment, f))
        if res is None or not res.ok:
            code = res.retcode if res else -1
            self.log("BLOCKED", f"ORDER FAILED {sym} {side} {plan['lots']} on {handle.name}: "
                     f"{RETCODE_TEXT.get(code, code)} {res.comment if res else ''}", sym)
            self.storage.save_decision(plan["candidate_id"], handle.name, sym, "FAILED",
                                       f"order_send {RETCODE_TEXT.get(code, code)}", candidate.score)
            return None
        fill = res.price
        slip = (fill - entry) * sign  # positive = worse than requested
        self.slippage = (self.slippage + [slip / spec.point if spec.point else 0.0])[-200:]
        tid = self.book.new_id()
        self.storage.save_fill(tid, handle.name, sym, side, res.volume or plan["lots"], entry, fill, slip, res.retcode,
                               res.attempts, res.filling, "open")
        t = {
            "id": tid, "account": handle.name, "mode": handle.mode, "symbol": sym, "direction": plan["direction"],
            "setup": candidate.setup, "timeframe": candidate.timeframe, "session": candidate.session,
            "regime": candidate.regime, "signal_id": candidate.id, "score": candidate.score, "open_ts": now,
            "close_ts": None, "entry": fill, "stop": plan["stop"], "initial_stop": plan["stop"], "tp1": plan["tp1"],
            "tp2": plan["tp2"], "volume": res.volume or plan["lots"], "volume_open": res.volume or plan["lots"],
            "exit_price": None, "pnl": 0.0, "r_multiple": None, "mfe_r": 0.0, "mae_r": 0.0, "exit_type": None,
            "exit_reason": None, "status": "open", "ticket": res.order, "risk_money": plan["risk_money"],
            "tp1_done": 0, "tp1_fraction": plan["tp1_fraction"], "tp2_fraction": plan["tp2_fraction"],
            "broker_tp": broker_tp, "realized_r": 0.0,
            "meta": {"zone_id": candidate.zone_id, "obstacle": plan["obstacle"], "slippage": slip,
                     "rr_planned": rr, "entry_requested": entry, "from_limit": from_limit,
                     "reasons": candidate.reasons, "size_factor": plan["size_factor"]},
        }
        # ---- post-fill RR check
        ok2, rr2, why2 = rr_check(plan["direction"], fill, plan["stop"], plan["tp1"], plan["tp2"], plan["obstacle"],
                                  self.cfg.min_rr)
        self.book.add(t)
        if self.tm is not None and ctx is not None:
            self.tm.set_time_targets(t, ctx, profile)
        self.storage.save_decision(candidate.id, handle.name, sym, "TAKEN", f"filled {fill:.6g} slip {slip:+.5g}",
                                   candidate.score)
        self.log("NEW TRADE", f"{handle.mode.upper()} {handle.name} {sym} {side.upper()} {t['volume']} @ {fill:.6g} "
                 f"SL {plan['stop']:.6g} TP1 {plan['tp1']:.6g} TP2 {plan['tp2']:.6g} RR {rr2.get('final', 0):.2f} "
                 f"risk {plan['risk_money']:.2f} ({candidate.setup} {candidate.timeframe} score {candidate.score:.0f})",
                 sym, {"trade_id": tid})
        if not ok2:
            self.log("EARLY EXIT", f"post-fill check: {why2} - closing {sym} immediately", sym)
            self.close(t, handle, 1.0, f"post-fill {why2}", "postfill", now)
            return None
        return t

    # ------------------------------------------------------------------ close / modify
    def _close_volume(self, t: dict, fraction: float, spec) -> float:
        vol_open = float(t["volume_open"])
        if fraction >= 0.999 or spec is None:
            return vol_open
        step = spec.volume_step or 0.01
        v = math.floor(vol_open * fraction / step + 1e-9) * step
        v = round(v, step_decimals(step))
        if v < spec.volume_min:
            # cannot split below the broker minimum: partial of volume_min if possible, else close all
            v = spec.volume_min if vol_open >= 2 * spec.volume_min - 1e-12 else vol_open
        if vol_open - v < spec.volume_min - 1e-12:
            v = vol_open   # remainder would be untradeable -> close everything
        return v

    def close(self, t: dict, handle, fraction: float, reason: str, exit_type: str, now: float,
              price_hint: float | None = None, rule: str | None = None) -> bool:
        trader = handle.trader if t["mode"] == handle.mode else (handle.adapter if t["mode"] == "live" else handle.paper)
        if trader is None:
            return False
        spec = trader.symbol_spec(t["symbol"])
        vol = self._close_volume(t, fraction, spec)
        full = vol >= float(t["volume_open"]) - 1e-9
        magic = int(handle.cfg.magic or self.ec["magic"])
        if t["mode"] == "paper":
            res = trader.close_position(t["ticket"], vol, int(self.ec["deviation_points"]), magic, 2,
                                        reason[:31], price=price_hint)
        else:
            res = self._send(trader, spec, lambda f: trader.close_position(t["ticket"], vol,
                                                                         int(self.ec["deviation_points"]), magic, f,
                                                                         reason[:31]))
        if res is None or not res.ok:
            self.log("ERROR", f"CLOSE FAILED {t['symbol']} {handle.name}: "
                     f"{RETCODE_TEXT.get(res.retcode, res.retcode) if res else 'n/a'}", t["symbol"])
            return False
        sign = 1 if t["direction"] == "long" else -1
        init_risk = abs(t["entry"] - t["initial_stop"]) or 1e-12
        r_part = (res.price - t["entry"]) * sign / init_risk
        frac_of_initial = vol / float(t["volume"])
        t["realized_r"] = float(t.get("realized_r") or 0.0) + r_part * frac_of_initial
        pnl_part = (res.price - t["entry"]) * sign / spec.tick_size * spec.tick_value * vol if spec else 0.0
        t["pnl"] = float(t.get("pnl") or 0.0) + pnl_part
        t["volume_open"] = round(float(t["volume_open"]) - vol, 8)
        self.storage.save_fill(t["id"], handle.name, t["symbol"], "sell" if sign > 0 else "buy", vol,
                               price_hint or res.price, res.price, (res.price - (price_hint or res.price)) * -sign,
                               res.retcode, res.attempts, res.filling, exit_type)
        tag = "EARLY EXIT" if exit_type in ("early", "time", "postfill") else ("CLOSE" if full else "MANAGE")
        if full or t["volume_open"] <= 1e-9:
            t["status"] = "closed"
            t["close_ts"] = now
            t["exit_price"] = res.price
            t["r_multiple"] = round(t["realized_r"], 3)
            t["exit_type"] = exit_type
            t["exit_reason"] = reason
            self.book.close(t)
            self.log(tag, f"{'EARLY EXIT' if tag == 'EARLY EXIT' else 'CLOSE'} {t['symbol']} {fmt_r(t['r_multiple'])}: "
                     f"{reason} ({handle.name}, P/L {t['pnl']:+.2f})", t["symbol"], {"trade_id": t["id"]})
            if exit_type in ("early", "time") and self.tm is not None:
                self.tm.open_shadow(t, t["r_multiple"], rule or exit_type, now)
        else:
            self.book.save(t)
            self.log(tag if tag != "CLOSE" else "MANAGE", f"PARTIAL {t['symbol']} {vol} lots {fmt_r(r_part)}: {reason} "
                     f"({handle.name}, {t['volume_open']} left)", t["symbol"], {"trade_id": t["id"]})
        return True

    def modify_sl(self, t: dict, handle, sl: float, reason: str) -> bool:
        trader = handle.adapter if t["mode"] == "live" else handle.paper
        if trader is None:
            return False
        tp = 0.0 if t.get("tp2_done") else t.get("broker_tp", t["tp2"])
        res = trader.modify_sltp(t["ticket"], sl, tp)
        if not res.ok:
            self.log("ERROR", f"modify SL failed {t['symbol']}: {res.comment}", t["symbol"])
            return False
        t["stop"] = sl
        self.book.save(t)
        self.log("MANAGE", f"{t['symbol']} SL -> {sl:.6g}: {reason} ({handle.name})", t["symbol"], {"trade_id": t["id"]})
        return True
