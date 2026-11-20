"""GUARDIAN - discipline coach with veto power. Its rules cannot be relaxed by any agent.

Rules: global kill switch, account pause, live-on-fallback refusal, daily max loss %, max trades/day
(and per symbol), max concurrent positions, total open-risk cap (lower-ranked setups sized down to
>=50% or skipped), correlation / same-currency / USD exposure filter, max spread & spread spike,
session hours, news blackout, consecutive-loss day lockout, revenge-trading block, overtrading
(trades/hour, symbol cooldown), Monday-open / Friday-close filters, daily profit lock, DXY block,
temporarily disabled setups (Analyst), score floor.
"""
from __future__ import annotations

import numpy as np

from aurum_nexus.bots.base import Bot
from aurum_nexus.symbols import currency_exposure, prior_correlation, usd_direction
from aurum_nexus.timeutil import in_window, is_weekend, minutes_since_week_open, to_dt


class Guardian(Bot):
    name = "GUARDIAN"
    color = "#ff3b3b"
    job = "Veto: loss limits, exposure, spreads, news, sessions, discipline"

    def __init__(self, bus, cfg, book, news=None, risk=None, clock=None):
        super().__init__(bus, cfg, clock)
        self.g = cfg.get("guardian")
        self.book = book
        self.news = news
        self.risk = risk
        self.kill_switch = bool(self.g.get("kill_switch", False))
        self.lockouts: dict[str, str] = {}
        self.lockout_day: dict[str, float] = {}
        self.vetoes: list[dict] = []

    def config_view(self) -> dict:
        return dict(self.g)

    def set_kill(self, on: bool, by: str = "user") -> None:
        self.kill_switch = bool(on)
        self.log("BLOCKED" if on else "SYSTEM", f"GLOBAL KILL SWITCH {'ON' if on else 'OFF'} ({by})")

    # ------------------------------------------------------------------ helpers
    def open_risk_pct(self, account: str, equity: float) -> float:
        tot = 0.0
        for t in self.book.open_for(account):
            sign = 1 if t["direction"] == "long" else -1
            risk_now = (t["entry"] - t["stop"]) * sign  # <=0 once stop is at/through breakeven
            init = abs(t["entry"] - t["initial_stop"]) or 1e-12
            frac_open = (t.get("volume_open") or 0) / (t.get("volume") or 1)
            tot += max(0.0, (t.get("risk_money") or 0.0) * frac_open * max(0.0, risk_now) / init)
        return 100.0 * tot / equity if equity else 0.0

    def _correlation_block(self, c, account: str, selected: list, corr_live: dict | None) -> str | None:
        thr = float(self.cfg.get("symbols.correlation_block_threshold", 0.75))
        others = [(t["symbol"], t["direction"]) for t in self.book.open_for(account)] + \
                 [(s.symbol, s.direction) for s in selected]
        usd_n = 0
        cur_n: dict[str, int] = {}
        my_usd = usd_direction(c.symbol, c.direction)
        my_exp = currency_exposure(c.symbol, c.direction)
        for sym, d in others:
            if sym == c.symbol:
                if d == c.direction:
                    return f"already {d} {sym}"
                continue
            corr = (corr_live or {}).get((c.symbol, sym))
            if corr is None:
                corr = prior_correlation(c.symbol, sym)
            same_way = (d == c.direction and corr >= thr) or (d != c.direction and corr <= -thr)
            if same_way:
                return f"correlated with open {sym} {d} (corr {corr:+.2f})"
            if my_usd != 0 and usd_direction(sym, d) == my_usd:
                usd_n += 1
            for cur, sgn in currency_exposure(sym, d).items():
                if my_exp.get(cur) == sgn:
                    cur_n[cur] = cur_n.get(cur, 0) + 1
        if usd_n + 1 > int(self.cfg.get("symbols.usd_exposure_max_positions", 3)):
            return f"USD exposure limit ({usd_n + 1} positions same USD direction)"
        lim = int(self.cfg.get("symbols.same_currency_max_positions", 2))
        for cur, n in cur_n.items():
            if cur != "USD" and n + 1 > lim:
                return f"same-currency exposure {cur} ({n + 1} > {lim})"
        return None

    # ------------------------------------------------------------------ main check
    def check(self, c, handle, ctx, profile: dict, now: float, selected: list, corr_live: dict | None = None,
              planned_risk_pct: float | None = None) -> dict:
        g = self.g
        acc = handle.name
        v = {"allowed": True, "reason": "ok", "size_factor": 1.0}

        def veto(reason: str) -> dict:
            v.update(allowed=False, reason=reason)
            self.count("veto")
            self.vetoes = (self.vetoes + [{"ts": now, "account": acc, "symbol": c.symbol, "reason": reason}])[-50:]
            return v

        if self.kill_switch:
            return veto("KILL SWITCH active")
        if handle.paused:
            return veto("account paused")
        if handle.live and ctx.preview:
            return veto("live trading refused on fallback/preview data")
        if c.score < self.cfg.min_score + float(profile.get("min_score_offset", 0)):
            return veto(f"score {c.score:.0f} below min {self.cfg.min_score + float(profile.get('min_score_offset', 0)):.0f}")
        if c.modifiers.get("dxy_block"):
            return veto("DXY breaking against the trade")
        if c.modifiers.get("setup_disabled"):
            return veto(f"setup {c.setup} temporarily disabled by Analyst (probation)")
        day = self.book.day_start(now)
        if self.lockout_day.get(acc) is not None and self.lockout_day[acc] != day:
            self.lockouts.pop(acc, None)           # day lockouts expire at the next broker day
            self.lockout_day.pop(acc, None)
        if acc in self.lockouts:
            return veto(self.lockouts[acc])
        if is_weekend(now):
            return veto("market closed (weekend)")
        lim = handle.cfg.limits or {}
        day_start = self.book.day_start(now)
        equity = handle.equity() or 0.0
        # daily loss
        if equity > 0:
            if handle.live:
                realized = handle.realized_today or 0.0
                floating = sum(p.profit for p in handle.positions if p.magic == self.cfg.get("execution.magic"))
            else:
                ps = handle.paper.account()
                realized = sum(d.net for d in handle.paper.deals(day_start, now + 60) if d.entry != "in")
                floating = ps.profit or 0.0
            start_eq = equity - realized - floating
            dl = 100.0 * (realized + floating) / start_eq if start_eq > 0 else 0.0
            maxloss = float(lim.get("daily_max_loss_pct", g["daily_max_loss_pct"]))
            if dl <= -maxloss:
                self.lockouts[acc] = f"daily max loss {dl:.2f}% <= -{maxloss}% - locked for the day"
                self.lockout_day[acc] = day
                self.log("BLOCKED", f"{acc}: {self.lockouts[acc]}")
                return veto(self.lockouts[acc])
        # trade counts
        today = self.book.opened_since(acc, day_start)
        if len(today) >= int(lim.get("max_trades_per_day", g["max_trades_per_day"])):
            return veto(f"max trades/day reached ({len(today)})")
        if sum(1 for t in today if t["symbol"] == c.symbol) >= int(g["max_trades_per_symbol_per_day"]):
            return veto(f"max trades/day on {c.symbol} reached")
        hour = self.book.opened_since(acc, now - 3600)
        if len(hour) >= int(g["max_trades_per_hour"]):
            return veto(f"overtrading: {len(hour)} trades in the last hour")
        recent_sym = self.book.opened_since(acc, now - 60 * float(g["symbol_cooldown_minutes"]), c.symbol)
        if recent_sym:
            return veto(f"cooldown on {c.symbol} ({g['symbol_cooldown_minutes']}m)")
        open_n = len(self.book.open_for(acc)) + sum(1 for s in selected if getattr(s, "_acc", None) == acc
                                                     and getattr(s, "_pending", False))
        if open_n >= int(lim.get("max_concurrent_positions", g["max_concurrent_positions"])):
            return veto(f"max concurrent positions ({open_n})")
        # consecutive losses
        cl = self.book.consecutive_losses(acc, day_start)
        if cl >= int(g["consecutive_losses_lockout"]):
            self.lockouts[acc] = f"{cl} consecutive losses - locked for the day"
            self.lockout_day[acc] = day
            self.log("BLOCKED", f"{acc}: {self.lockouts[acc]}")
            return veto(self.lockouts[acc])
        # revenge block
        so = self.book.last_stopout(acc, c.symbol)
        if so and now - so < 60 * float(g["revenge_block_minutes"]):
            return veto(f"revenge block: stopped out on {c.symbol} {(now - so) / 60:.0f}m ago")
        # profit lock
        if self.risk is not None:
            locked, why = self.risk.profit_lock(acc, self.book.realized_r_today(acc, now))
            if locked:
                return veto(why)
        # correlation / exposure
        why = self._correlation_block(c, acc, selected, corr_live)
        if why:
            return veto(why)
        # spread
        tick = ctx.tick
        atr5 = ctx.atr("M5")
        if tick is not None and atr5:
            if tick.spread > float(g["max_spread_atr_frac"]) * atr5:
                return veto(f"spread {tick.spread:.5g} > {g['max_spread_atr_frac']} x ATR(M5)")
            if len(ctx.spread_hist) >= 30:
                med = float(np.median(ctx.spread_hist[-300:]))
                if med > 0 and tick.spread > float(g["spread_spike_mult"]) * med:
                    return veto(f"spread spike {tick.spread / med:.1f}x median")
        # session hours
        sess = profile.get("sessions_allowed", [])
        sessions = self.cfg.get("sessions")
        if sess and not any(in_window(now, sessions[s]) for s in sess if s in sessions and "end" in sessions[s]):
            return veto(f"outside allowed sessions {sess}")
        # news
        if self.news is not None:
            ev = self.news.blackout(c.symbol, now)
            if ev:
                return veto(f"news blackout: {ev['title']} ({ev['country']})")
            if self.news.unavailable_blocks():
                return veto("news feed unavailable (on_unavailable=block)")
        # Monday open / Friday close
        if minutes_since_week_open(now) < float(g["monday_open_block_minutes"]):
            return veto("Monday open filter")
        d = to_dt(now)
        if d.weekday() == 4 and d.hour >= int(g["friday_close_block_hour_utc"]):
            return veto("Friday close filter")
        # total open-risk cap (size down to >=50% or skip)
        if planned_risk_pct is not None and equity > 0:
            cap = float(g["max_open_risk_pct"])
            used = self.open_risk_pct(acc, equity) + sum(getattr(s, "_risk_pct", 0.0) for s in selected
                                                          if getattr(s, "_acc", None) == acc
                                                          and getattr(s, "_pending", False))
            room = cap - used
            if room <= 0:
                return veto(f"open-risk cap {cap}% reached ({used:.2f}% in use)")
            if planned_risk_pct > room:
                f = room / planned_risk_pct
                if f < float(g["min_size_fraction_under_cap"]):
                    return veto(f"open-risk cap: only {room:.2f}% left ({f:.0%} of planned size < 50%)")
                v["size_factor"] = f
                v["reason"] = f"sized down to {f:.0%} by open-risk cap ({used:.2f}% in use)"
        return v
