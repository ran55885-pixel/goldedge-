"""Coordinator / ranking layer.

Every cycle: rank all candidates by score and take EVERY candidate >= min_score in score order (it can be
several at once - it depends on the market), subject to the max-concurrent ceiling (a ceiling, not a
target), the total open-risk cap, the correlation filter and the Guardian veto. Weak markets therefore
produce fewer trades. Signal once, execute many: each selected candidate is executed on every enabled
account trading that symbol, with the account's own sizing and levels re-based to its broker price.
"""
from __future__ import annotations

from collections import deque


class Coordinator:
    def __init__(self, cfg, bus, storage, accounts, risk, guardian, execution, strategist, feed, contexts_fn):
        self.cfg = cfg
        self.bus = bus
        self.storage = storage
        self.accounts = accounts
        self.risk = risk
        self.guardian = guardian
        self.execution = execution
        self.strategist = strategist
        self.feed = feed
        self.contexts_fn = contexts_fn
        self.ranking: deque = deque(maxlen=40)
        self.corr_live: dict = {}

    def _accounts_for(self, symbol: str):
        for h in self.accounts.handles.values():
            if not h.cfg.enabled:
                continue
            if h.cfg.symbols and symbol not in h.cfg.symbols:
                continue
            yield h

    def _offset(self, h, symbol: str, analysis_mid: float | None) -> tuple[float, float | None, float | None]:
        """(offset, bid, ask) of this account's broker price vs the analysis feed."""
        t = h.trader.tick(symbol) if h.trader is not None else None
        if t is None:
            return 0.0, None, None
        off = (t.mid - analysis_mid) if (analysis_mid and h.connected and h.adapter is not None
                                         and getattr(h.adapter, "kind", "") == "mt5") else 0.0
        return off, t.bid, t.ask

    def select(self, candidates: list, now: float) -> list[dict]:
        results = []
        ctxs = self.contexts_fn()
        selected = []
        for c in sorted(candidates, key=lambda x: -x.score):
            prof = self.strategist.profile(c.symbol)
            min_score = self.cfg.min_score + float(prof.get("min_score_offset", 0))
            self.storage.save_signal(c)
            if c.score < min_score:
                c.status, c.status_reason = "SKIPPED", f"score {c.score:.0f} below min {min_score:.0f}"
                self.bus.emit("SKIP", "SIGNAL", f"SKIPPED {c.symbol} score {c.score:.0f} (below min {min_score:.0f})",
                              c.symbol)
                self.storage.save_decision(c.id, "*", c.symbol, "SKIPPED", c.status_reason, c.score, now)
                self.ranking.appendleft(self._row(c))
                results.append({"id": c.id, "status": c.status, "reason": c.status_reason})
                continue
            dup = next((x for x in selected if x.symbol == c.symbol), None)
            if dup is not None:
                c.status, c.status_reason = "SKIPPED", f"lower-ranked duplicate ({c.symbol} already selected this cycle)"
                self.storage.save_decision(c.id, "*", c.symbol, "SKIPPED", c.status_reason, c.score, now)
                self.ranking.appendleft(self._row(c))
                results.append({"id": c.id, "status": c.status, "reason": c.status_reason})
                continue
            ctx = ctxs.get(c.symbol)
            analysis_mid = ctx.tick.mid if ctx and ctx.tick else None
            taken, reasons = 0, []
            pending = 0
            blocked = False
            for h in self._accounts_for(c.symbol):
                if h.trader is None:
                    reasons.append(f"{h.name}: no back-end")
                    continue
                spec = h.trader.symbol_spec(c.symbol)
                off, bid, ask = self._offset(h, c.symbol, analysis_mid)
                eq = h.equity()
                if spec is None or bid is None or not eq:
                    reasons.append(f"{h.name}: no spec/quote/equity")
                    continue
                factor, fnotes = self.risk.size_factor(self.execution.book.closed(h.name), eq, h.peak_equity)
                plan, why = self.risk.plan(c, h.name, h.cfg.risk_pct, eq, spec, bid, ask, off, factor, prof)
                if plan is None:
                    reasons.append(f"{h.name}: {why}")
                    self.storage.save_decision(c.id, h.name, c.symbol, "SKIPPED", why, c.score, now)
                    continue
                v = self.guardian.check(c, h, ctx, prof, now, selected, self.corr_live, plan["risk_pct"])
                if not v["allowed"]:
                    blocked = True
                    reasons.append(f"{h.name}: {v['reason']}")
                    self.storage.save_decision(c.id, h.name, c.symbol, "BLOCKED", v["reason"], c.score, now)
                    self.bus.emit("BLOCKED", "GUARDIAN", f"BLOCKED {c.symbol} {c.direction} {c.setup} score "
                                  f"{c.score:.0f} on {h.name}: {v['reason']}", c.symbol)
                    continue
                if v["size_factor"] < 1.0:
                    c.size_factor = v["size_factor"]
                    plan, why = self.risk.plan(c, h.name, h.cfg.risk_pct, eq, spec, bid, ask, off, factor, prof)
                    c.size_factor = 1.0
                    if plan is None:
                        reasons.append(f"{h.name}: {why}")
                        continue
                    self.bus.emit("MANAGE", "GUARDIAN", f"{c.symbol} on {h.name}: {v['reason']}", c.symbol)
                if fnotes:
                    plan["note"] += " · size " + ", ".join(fnotes)
                self.bus.emit("SELECTED", "SIGNAL", f"SELECTED {c.symbol} {c.direction} {c.setup} score {c.score:.0f} "
                              f"-> {h.name} {plan['lots']} lots risk {plan['risk_pct']:.2f}%", c.symbol)
                t = self.execution.submit(plan, h, c, ctx, prof, now)
                marker = type("Sel", (), {})()
                marker.symbol, marker.direction, marker._acc, marker._risk_pct = c.symbol, c.direction, h.name, plan["risk_pct"]
                marker._pending = t is None   # opened trades are already in the trade book
                selected.append(marker)
                if t:
                    taken += 1
                else:
                    pending += 1
            if taken:
                c.status, c.status_reason = "TAKEN", f"{taken} account(s)"
            elif pending:
                c.status, c.status_reason = "PENDING", "virtual limit / deferred"
            else:
                c.status = "BLOCKED" if blocked else "SKIPPED"
                c.status_reason = "; ".join(reasons)[:300] or "no eligible account"
            self.ranking.appendleft(self._row(c))
            results.append({"id": c.id, "status": c.status, "reason": c.status_reason})
        return results

    @staticmethod
    def _row(c) -> dict:
        return {"id": c.id, "ts": c.created, "symbol": c.symbol, "direction": c.direction, "setup": c.setup,
                "timeframe": c.timeframe, "score": c.score, "base_score": c.base_score, "rr": c.rr_final,
                "rr_tp1": c.rr_tp1, "rr_obstacle": c.rr_obstacle, "entry": c.entry, "stop": c.stop, "tp1": c.tp1,
                "tp2": c.tp2, "entry_type": c.entry_type, "status": c.status, "reason": c.status_reason,
                "components": c.components, "modifiers": c.modifiers, "confirmations": c.confirmations,
                "notes": c.reasons[:6], "session": c.session, "regime": c.regime}
