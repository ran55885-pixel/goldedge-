"""TRADE MANAGER (part of the RISK bot) - early exits & time targets.

Every open position is re-evaluated each cycle. Exits: TP1 partial + breakeven, TP2 at the opposing
obstacle, runner trailed by M5 structure. Early-exit rules (configurable, by priority):
friday_close, news_ahead, end_of_day, end_of_session (off by default), ltf_choch, htf_shift,
opposing_rejection, momentum_fade, cvd_divergence, vwap_band_rejection, dxy_shock, giveback,
spread_spike, volatility_spike, plus time targets (T1: reach +0.5R in time, T2: reach TP1 in time).
Early exits may close below 1R (the 1:1 floor applies to entries only). After every early/time exit a
"shadow" of the original plan is tracked to measure whether the exit helped.
"""
from __future__ import annotations

import uuid
import numpy as np

from aurum_nexus.indicators import candles
from aurum_nexus.logbus import fmt_r
from aurum_nexus.symbols import DXY_RELATION
from aurum_nexus.timeutil import to_dt, window_bounds


class TradeManager:
    def __init__(self, bot, cfg, levels=None, news=None, storage=None):
        self.bot = bot
        self.cfg = cfg
        self.tm = cfg.get("trade_manager")
        self.tt = cfg.get("time_targets")
        self.levels = levels
        self.news = news
        self.storage = storage
        self.fired: dict[str, int] = {}
        self.feed: list[dict] = []
        self.shadows: dict[str, dict] = {}

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def r_now(trade: dict, price: float) -> float:
        sign = 1 if trade["direction"] == "long" else -1
        risk = abs(trade["entry"] - trade["initial_stop"]) or 1e-12
        return (price - trade["entry"]) * sign / risk

    def pace(self, ctx, session: str) -> float | None:
        a5 = ctx.atr("M5") if ctx else None
        if not a5:
            return None
        f = float(self.tt["session_pace"].get(session, self.tt["session_pace"].get("off", 0.5)))
        return a5 / 5.0 * f  # price units per minute

    def set_time_targets(self, trade: dict, ctx, profile: dict | None) -> None:
        if not self.tt["enabled"]:
            return
        p = self.pace(ctx, ctx.session if ctx else "off")
        if not p:
            return
        tt = (profile or {}).get("time_targets", {})
        t1m = float(tt.get("t1_mult", self.tt["t1_mult"]))
        t2m = float(tt.get("t2_mult", self.tt["t2_mult"]))
        risk = abs(trade["entry"] - trade["initial_stop"])
        d1 = float(self.tt["t1_r"]) * risk
        d2 = abs(trade["tp1"] - trade["entry"])
        lo = float((self.tt.get("min_minutes_by_tf") or {}).get(trade.get("timeframe"), self.tt["min_minutes"]))
        hi = float(self.tt["max_minutes"])
        trade["t1_minutes"] = max(lo, min(hi, t1m * d1 / p))
        trade["t2_minutes"] = max(trade["t1_minutes"], max(lo, min(hi, t2m * d2 / p)))
        trade["t1_deadline"] = trade["open_ts"] + 60 * trade["t1_minutes"]
        trade["t2_deadline"] = trade["open_ts"] + 60 * trade["t2_minutes"]

    def eta(self, trade: dict, ctx, price: float, now: float) -> dict:
        elapsed = (now - trade["open_ts"]) / 60.0
        out = {"elapsed_min": round(elapsed, 1)}
        p = self.pace(ctx, ctx.session if ctx else "off") if ctx else None
        if p and not trade.get("tp1_done"):
            dist = max(0.0, (trade["tp1"] - price) * (1 if trade["direction"] == "long" else -1))
            out["tp1_eta_min"] = round(dist / p, 1)
        for k in ("t1_deadline", "t2_deadline"):
            if trade.get(k):
                out[k.replace("_deadline", "_left_min")] = round((trade[k] - now) / 60.0, 1)
        out["text"] = (f"TP1 ETA {out['tp1_eta_min']:.0f}m · elapsed {elapsed:.0f}m" if "tp1_eta_min" in out
                       else f"elapsed {elapsed:.0f}m")
        return out

    def _fire(self, rule: str, trade: dict, action: str, fraction: float, reason: str, price: float) -> dict:
        self.fired[rule] = self.fired.get(rule, 0) + 1
        r = self.r_now(trade, price)
        ev = {"rule": rule, "action": action, "fraction": fraction, "reason": reason, "r": r,
              "trade_id": trade["id"], "symbol": trade["symbol"]}
        return ev

    # ------------------------------------------------------------------ main evaluation
    def evaluate(self, trade: dict, ctx, bid: float, ask: float, now: float, dxy_state: dict | None) -> list[dict]:
        """Return a list of actions: {'action': 'close'|'modify_sl', 'fraction', 'sl', 'exit_type', 'reason'}"""
        long = trade["direction"] == "long"
        price = bid if long else ask          # price at which we would exit
        sign = 1 if long else -1
        r = self.r_now(trade, price)
        trade["mfe_r"] = max(trade.get("mfe_r") or 0.0, r)
        trade["mae_r"] = min(trade.get("mae_r") or 0.0, r)
        trade["last_price"] = price
        trade["r_now"] = r
        acts: list[dict] = []
        # ---- protective stop (paper: simulated; live: broker SL also exists)
        if (price - trade["stop"]) * sign <= 0:
            et = "breakeven" if trade.get("be_done") and not trade.get("trailing") else (
                "trail" if trade.get("trailing") else "sl")
            return [{"action": "close", "fraction": 1.0, "exit_type": et, "reason": f"{et.upper()} hit at {price:.6g}",
                     "price": trade["stop"] if trade["mode"] == "paper" else price}]
        # ---- TP1 partial + breakeven
        if not trade.get("tp1_done") and (price - trade["tp1"]) * sign >= 0:
            frac = trade.get("tp1_fraction", 0.5) if trade["tp2"] != trade["tp1"] else 1.0
            acts.append({"action": "close", "fraction": frac, "exit_type": "tp1", "reason": f"TP1 {fmt_r(r)}",
                         "price": trade["tp1"] if trade["mode"] == "paper" else price, "mark": "tp1_done"})
            if frac < 1.0 and self.cfg.get("risk.move_be_after_tp1", True):
                spread = ask - bid
                be = trade["entry"] + sign * spread
                acts.append({"action": "modify_sl", "sl": be, "reason": "TP1 hit -> stop to breakeven", "mark": "be_done"})
            return acts
        # ---- TP2 partial (runner keeps trailing)
        if trade.get("tp1_done") and not trade.get("tp2_done") and (price - trade["tp2"]) * sign >= 0:
            init = trade["volume"]
            remain = trade["volume_open"]
            frac_init = trade.get("tp2_fraction", 0.3)
            frac = min(1.0, frac_init * init / remain) if remain > 0 else 1.0
            if remain - frac * remain < 1e-9 or frac >= 0.999:
                frac = 1.0
            acts.append({"action": "close", "fraction": frac, "exit_type": "tp2", "reason": f"TP2 {fmt_r(r)}",
                         "price": trade["tp2"] if trade["mode"] == "paper" else price, "mark": "tp2_done"})
            return acts
        # ---- runner trail by M5 structure
        if trade.get("tp2_done") or (trade.get("tp1_done") and trade["tp1"] == trade["tp2"]):
            m5 = ctx.tf.get("M5") if ctx else None
            if m5 is not None and m5.structure and m5.atr:
                buf = float(self.tm["trail"]["buffer_atr_mult"]) * m5.atr
                sw = m5.structure.get("last_low") if long else m5.structure.get("last_high")
                if sw is not None and sw.time > trade["open_ts"]:
                    new_sl = sw.price - buf if long else sw.price + buf
                    if (new_sl - trade["stop"]) * sign > 0 and (price - new_sl) * sign > 0:
                        acts.append({"action": "modify_sl", "sl": new_sl, "reason": f"trail to M5 swing {sw.price:.6g}",
                                     "mark": "trailing"})
        # ---- early-exit rules by priority
        rules = sorted(((k, v) for k, v in self.tm["rules"].items() if v.get("enabled")), key=lambda kv: kv[1]["priority"])
        for name, rc in rules:
            fn = getattr(self, f"_rule_{name}", None)
            if fn is None:
                continue
            try:
                res = fn(trade, ctx, rc, price, r, now, bid, ask, dxy_state)
            except Exception as e:  # rule failure must never crash management
                self.bot.log("ERROR", f"TM rule {name} failed: {e}", trade["symbol"])
                res = None
            if res:
                action, frac, reason = res
                if action == "close" and frac < 1.0 and trade.get(f"rule_{name}_done"):
                    continue
                self.fired[name] = self.fired.get(name, 0) + 1
                if action == "close":
                    acts.append({"action": "close", "fraction": frac, "exit_type": "early", "rule": name,
                                 "reason": reason, "price": price, "mark": f"rule_{name}_done"})
                else:
                    acts.append({"action": "modify_sl", "sl": frac, "reason": reason, "rule": name})
                return acts
        # ---- time targets
        if self.tt["enabled"] and trade.get("t1_deadline"):
            if not trade.get("t1_hit") and r >= float(self.tt["t1_r"]):
                trade["t1_hit"] = True
            if not trade.get("t1_hit") and not trade.get("t1_missed") and now > trade["t1_deadline"]:
                trade["t1_missed"] = True
                self.fired["time_t1"] = self.fired.get("time_t1", 0) + 1
                mode = self.tt["on_t1_miss"]
                if mode == "tighten":
                    risk = abs(trade["entry"] - trade["initial_stop"])
                    new_sl = trade["entry"] + sign * float(self.tt["t1_tighten_to_r"]) * risk
                    if (new_sl - trade["stop"]) * sign > 0 and (price - new_sl) * sign > 0:
                        acts.append({"action": "modify_sl", "sl": new_sl, "rule": "time_t1",
                                     "reason": f"T1 missed (+{self.tt['t1_r']}R not reached in {trade['t1_minutes']:.0f}m)"
                                               f" -> SL tightened to {self.tt['t1_tighten_to_r']}R"})
                elif mode == "partial":
                    acts.append({"action": "close", "fraction": float(self.tt["t1_partial"]), "exit_type": "time",
                                 "rule": "time_t1", "reason": f"T1 missed -> partial close", "price": price})
                if acts:
                    return acts
            if not trade.get("tp1_done") and now > trade.get("t2_deadline", 1e18) and self.tt["on_t2_miss"] == "close":
                self.fired["time_t2"] = self.fired.get("time_t2", 0) + 1
                acts.append({"action": "close", "fraction": 1.0, "exit_type": "time", "rule": "time_t2",
                             "reason": f"T2 missed (TP1 not reached in {trade['t2_minutes']:.0f}m)", "price": price})
        return acts

    # ------------------------------------------------------------------ rules (return (action, fraction|sl, reason) or None)
    def _rule_friday_close(self, t, ctx, rc, price, r, now, bid, ask, dxy):
        d = to_dt(now)
        if d.weekday() == 4 and (d.hour, d.minute) >= (int(rc["hour_utc"]), int(rc.get("minute", 0))):
            return "close", 1.0, "Friday close"
        return None

    def _rule_news_ahead(self, t, ctx, rc, price, r, now, bid, ask, dxy):
        if self.news is None:
            return None
        ev = self.news.upcoming(t["symbol"], now, int(rc["minutes"]))
        if not ev:
            return None
        if r < float(rc.get("min_r_to_hold", 1.0)):
            return "close", 1.0, f"news ahead: {ev['title']} ({ev['country']}) in {ev['minutes']:.0f}m"
        sign = 1 if t["direction"] == "long" else -1
        be = t["entry"] + sign * (ask - bid)
        if (be - t["stop"]) * sign > 0:
            return "modify_sl", be, f"news ahead ({ev['title']}): stop to breakeven"
        return None

    def _rule_end_of_day(self, t, ctx, rc, price, r, now, bid, ask, dxy):
        ro = self.cfg.get("sessions.rollover")
        last_start, _ = window_bounds(now, ro)   # most recent rollover start <= now
        nxt = last_start + 86400                  # next rollover start
        if 0 <= nxt - now <= 60 * int(rc["minutes_before_rollover"]):
            return "close", 1.0, f"end of day ({(nxt - now) / 60:.0f}m to rollover)"
        return None

    def _rule_end_of_session(self, t, ctx, rc, price, r, now, bid, ask, dxy):
        sess = t.get("session")
        if not sess or sess in ("off", "overlap"):
            return None
        w = self.cfg.get(f"sessions.{sess}")
        if not w:
            return None
        s, e = window_bounds(t["open_ts"], w)
        if now >= e:
            return "close", 1.0, f"end of {sess} session"
        return None

    def _since_entry_event(self, ctx, tf, t, against: str, kinds=("CHoCH",)):
        d = ctx.tf.get(tf) if ctx else None
        if d is None or not d.structure:
            return None
        for ev in reversed(d.structure.get("events", [])):
            if ev.time <= t["open_ts"]:
                return None
            if ev.direction == against and ev.kind in kinds:
                return ev
        return None

    def _rule_ltf_choch(self, t, ctx, rc, price, r, now, bid, ask, dxy):
        against = "bear" if t["direction"] == "long" else "bull"
        ev = self._since_entry_event(ctx, rc.get("timeframe", "M5"), t, against)
        if ev:
            return "close", float(rc.get("partial", 1.0)), f"opposing CHoCH {rc.get('timeframe', 'M5')}"
        return None

    def _rule_htf_shift(self, t, ctx, rc, price, r, now, bid, ask, dxy):
        against = "bear" if t["direction"] == "long" else "bull"
        ev = self._since_entry_event(ctx, rc.get("timeframe", "H1"), t, against, ("CHoCH", "BOS"))
        if ev:
            return "close", 1.0, f"HTF shift: {rc.get('timeframe', 'H1')} {ev.kind} against position"
        return None

    def _rule_opposing_rejection(self, t, ctx, rc, price, r, now, bid, ask, dxy):
        if self.levels is None or r <= 0.3 or ctx is None:
            return None
        m5 = ctx.tf.get("M5")
        if m5 is None or m5.n < 3:
            return None
        order = {"A+": 3, "A": 2, "B": 1, "C": 0}
        long = t["direction"] == "long"
        last = m5.bars.iloc[-1]
        for lv in self.levels.maps.get(t["symbol"], []):
            if order[lv["grade"]] < order[rc.get("min_grade", "A")]:
                continue
            touched = last.high >= lv["low"] and last.low <= lv["high"]
            if not touched:
                continue
            if long and lv["low"] > t["entry"] and candles.rejection(m5.bars) == "bear":
                return "close", float(rc.get("partial", 0.5)), f"opposing rejection at {lv['mid']:.6g} {lv['grade']}"
            if not long and lv["high"] < t["entry"] and candles.rejection(m5.bars) == "bull":
                return "close", float(rc.get("partial", 0.5)), f"opposing rejection at {lv['mid']:.6g} {lv['grade']}"
        return None

    def _rule_momentum_fade(self, t, ctx, rc, price, r, now, bid, ask, dxy):
        m5 = ctx.tf.get("M5") if ctx else None
        n = int(rc.get("bars", 6))
        if m5 is None or m5.n < n + 3 or r >= 0.5 or r <= -0.5:
            return None
        seg = m5.bars[m5.bars["time"] > t["open_ts"]]
        if len(seg) < n:
            return None
        long = t["direction"] == "long"
        ext_idx = int(seg["high"].to_numpy().argmax()) if long else int(seg["low"].to_numpy().argmin())
        if len(seg) - 1 - ext_idx < n:
            return None
        rng = (seg["high"] - seg["low"]).to_numpy()[-3:]
        if not (rng[2] <= rng[1] <= rng[0]):
            return None
        return "close", float(rc.get("partial", 0.5)), f"momentum fade ({n} bars without progress)"

    def _rule_cvd_divergence(self, t, ctx, rc, price, r, now, bid, ask, dxy):
        m5 = ctx.tf.get("M5") if ctx else None
        if m5 is None or not m5.divergences or r <= 0:
            return None
        want = "bearish" if t["direction"] == "long" else "bullish"
        for dv in m5.divergences[-3:]:
            if dv["type"] == want and dv["time"] > t["open_ts"] and m5.n - 1 - dv["idx"] <= 3:
                return "close", float(rc.get("partial", 0.5)), f"CVD {want} divergence against position (estimate)"
        return None

    def _rule_vwap_band_rejection(self, t, ctx, rc, price, r, now, bid, ask, dxy):
        dv = ctx.vwap.get("daily") if ctx else None
        m5 = ctx.tf.get("M5") if ctx else None
        if not dv or m5 is None or m5.n < 2 or r <= 0.5:
            return None
        last = m5.bars.iloc[-1]
        if t["direction"] == "long" and last.high >= dv["u2"][-1] and candles.rejection(m5.bars) == "bear":
            return "close", float(rc.get("partial", 0.5)), "rejection at +2σ daily VWAP"
        if t["direction"] == "short" and last.low <= dv["l2"][-1] and candles.rejection(m5.bars) == "bull":
            return "close", float(rc.get("partial", 0.5)), "rejection at -2σ daily VWAP"
        return None

    def _rule_dxy_shock(self, t, ctx, rc, price, r, now, bid, ask, dxy):
        if not dxy or not dxy.get("shock"):
            return None
        sh = dxy["shock"]
        if sh["time"] < t["open_ts"] or now - sh["time"] > 600:
            return None
        rel = DXY_RELATION.get(t["symbol"], 0)
        if rel == 0:
            return None
        usd_dir = rel * (1 if t["direction"] == "long" else -1)
        if (sh["direction"] == "up" and usd_dir < 0) or (sh["direction"] == "down" and usd_dir > 0):
            return "close", 1.0, f"DXY shock {sh['direction']} {sh['atr_mult']}xATR against position"
        return None

    def _rule_giveback(self, t, ctx, rc, price, r, now, bid, ask, dxy):
        mfe = t.get("mfe_r") or 0.0
        if mfe >= float(rc["min_mfe_r"]) and r <= mfe * (1 - float(rc["giveback_pct"]) / 100.0):
            return "close", 1.0, f"giveback: {fmt_r(r)} after MFE {fmt_r(mfe)}"
        return None

    def _rule_spread_spike(self, t, ctx, rc, price, r, now, bid, ask, dxy):
        if ctx is None or len(ctx.spread_hist) < 30:
            return None
        med = float(np.median(ctx.spread_hist[-300:]))
        if med <= 0 or (ask - bid) <= float(rc["mult"]) * med:
            return None
        if r > 0.5 and not t.get("rule_spread_spike_done"):
            return "close", 0.5, f"spread spike {(ask - bid) / med:.1f}x median - securing half"
        return None

    def _rule_volatility_spike(self, t, ctx, rc, price, r, now, bid, ask, dxy):
        m1 = ctx.tf.get("M1") if ctx else None
        if m1 is None or not m1.atr or m1.n < 2:
            return None
        last = m1.bars.iloc[-1]
        if last.time < t["open_ts"]:
            return None
        rng = float(last.high - last.low)
        against = (last.close < last.open) if t["direction"] == "long" else (last.close > last.open)
        if rng >= float(rc["atr_mult"]) * m1.atr and against:
            return "close", 1.0, f"volatility spike {rng / m1.atr:.1f}xATR(M1) against position"
        return None

    # ------------------------------------------------------------------ shadows
    def open_shadow(self, trade: dict, exit_r: float, rule: str, now: float) -> dict:
        sid = uuid.uuid4().hex[:10]
        sh = {"id": sid, "trade_id": trade["id"], "ts": now, "symbol": trade["symbol"],
              "direction": trade["direction"], "entry": trade["entry"], "stop": trade["initial_stop"],
              "tp": trade["tp2"], "risk": abs(trade["entry"] - trade["initial_stop"]), "exit_r": exit_r,
              "shadow_r": None, "status": "open", "closed_ts": None, "rule": rule}
        self.shadows[sid] = sh
        if self.storage is not None:
            self.storage.save_shadow(sh)
        return sh

    def update_shadows(self, prices: dict[str, tuple[float, float]], now: float, max_hours: float = 24) -> None:
        for sid, sh in list(self.shadows.items()):
            q = prices.get(sh["symbol"])
            if not q:
                continue
            bid, ask = q
            long = sh["direction"] == "long"
            px = bid if long else ask
            sign = 1 if long else -1
            r = (px - sh["entry"]) * sign / (sh["risk"] or 1e-12)
            done = None
            if (px - sh["stop"]) * sign <= 0:
                done = -1.0
            elif (px - sh["tp"]) * sign >= 0:
                done = (sh["tp"] - sh["entry"]) * sign / (sh["risk"] or 1e-12)
            elif now - sh["ts"] > max_hours * 3600:
                done = r
            if done is not None:
                sh["shadow_r"] = round(done, 3)
                sh["status"] = "closed"
                sh["closed_ts"] = now
                if self.storage is not None:
                    self.storage.save_shadow(sh)
                del self.shadows[sid]
                diff = sh["exit_r"] - sh["shadow_r"]
                self.bot.log("ANALYST", f"shadow {sh['symbol']} ({sh['rule']}): exit {fmt_r(sh['exit_r'])} vs held "
                             f"{fmt_r(sh['shadow_r'])} -> early exit {'helped' if diff >= 0 else 'cost'} {abs(diff):.2f}R",
                             sh["symbol"])
