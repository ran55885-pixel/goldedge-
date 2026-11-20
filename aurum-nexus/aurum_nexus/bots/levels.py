"""LEVELS - live Daily Levels Agent (DXY-aware).

Builds (at broker-day open) and updates live the day's key-level map from S/D zones, PDH/PDL/PWH/PWL,
daily/weekly open, Asia H/L, composite & prior-day POC/VAH/VAL, HVN/LVN, weekly VWAP ±σ, untested
FVG/OB, EQH/EQL liquidity, pivot S/R and round numbers. Levels within ATR×k are clustered into
confluence zones, scored 0-100 and graded A+/A/B.

Score (sum 100): confluence 30 · timeframe weight 20 · freshness 15 · prior reaction volume 15 ·
                 distance 10 · intrinsic quality 10
State machine: WAITING -> WATCHING (within X·ATR) -> READY (valid live reaction) / STALLED (no reaction
within N bars) -> BROKEN (2+ M5 closes beyond + volume -> role flip).
"""
from __future__ import annotations

import numpy as np

from aurum_nexus import dxy as dxym
from aurum_nexus.bots.base import Bot
from aurum_nexus.context import SymbolContext
from aurum_nexus.indicators import atr as atrm
from aurum_nexus.indicators import candles, levels_basic, structure
from aurum_nexus.indicators import vwap as vwm
from aurum_nexus.models import Level
from aurum_nexus.symbols import DXY_RELATION
from aurum_nexus.timeutil import broker_day_start

TF_WEIGHT = {"W1": 1.0, "D1": 1.0, "H4": 0.9, "H1": 0.8, "M15": 0.6, "M5": 0.45, "M1": 0.3}
KIND_LABEL = {"demand": "demand", "supply": "supply", "fvg_bull": "FVG+", "fvg_bear": "FVG-", "ob_bull": "OB+",
              "ob_bear": "OB-", "eqh": "EQH", "eql": "EQL", "sr": "S/R", "round": "round"}


def grade_for(score: float, th: dict) -> str:
    if score >= th["A+"]:
        return "A+"
    if score >= th["A"]:
        return "A"
    if score >= th["B"]:
        return "B"
    return "C"


class LevelsAgent(Bot):
    name = "LEVELS"
    color = "#ffd166"
    job = "Daily key-level map, confluence grades, live reaction states, DXY"

    def __init__(self, bus, cfg, feed, clock=None):
        super().__init__(bus, cfg, clock or feed.now)
        self.feed = feed
        self.lc = cfg.get("levels")
        self.tt = cfg.get("time_targets")
        self.maps: dict[str, list[dict]] = {}
        self.states: dict[str, dict[str, dict]] = {}
        self.built_day: dict[str, float] = {}
        self.dxy = DxyAnalyzer(cfg, feed, bus)

    def config_view(self) -> dict:
        return {**self.lc, "reaction_bars": self.tt["reaction_bars"], "time_targets": self.tt["enabled"]}

    # ------------------------------------------------------------------ candidates
    def _candidates(self, ctx: SymbolContext) -> list[tuple[Level, str]]:
        out: list[tuple[Level, str]] = []
        for tf, d in ctx.tf.items():
            for z in d.zones:
                out.append((z, f"{tf} {z.kind}"))
            if tf in ("D1", "H4", "H1", "M15"):
                for f in d.fvgs:
                    if f.fresh:
                        out.append((f, f"{tf} {KIND_LABEL[f.kind]}"))
                for o in d.obs:
                    if o.fresh:
                        out.append((o, f"{tf} {KIND_LABEL[o.kind]}"))
            if tf in ("H1", "M15", "M5"):
                for lq in d.liquidity:
                    if not lq.meta.get("swept"):
                        out.append((lq, f"{tf} {KIND_LABEL[lq.kind]}"))
            if tf in ("D1", "H4", "H1"):
                for p in d.pivots:
                    out.append((p, f"{tf} S/R x{p.touches}"))
        dl = ctx.daily or {}
        for key, label, tf, strength in (("pdh", "PDH", "D1", 0.75), ("pdl", "PDL", "D1", 0.75),
                                         ("pwh", "PWH", "W1", 0.85), ("pwl", "PWL", "W1", 0.85),
                                         ("daily_open", "Daily open", "D1", 0.5),
                                         ("weekly_open", "Weekly open", "W1", 0.55)):
            v = dl.get(key)
            if v:
                out.append((Level(kind=key, low=v, high=v, timeframe=tf, strength=strength), label))
        if ctx.asia:
            out.append((Level(kind="asia_high", low=ctx.asia["high"], high=ctx.asia["high"], timeframe="H1",
                              strength=0.6, side="resistance"), "Asia high"))
            out.append((Level(kind="asia_low", low=ctx.asia["low"], high=ctx.asia["low"], timeframe="H1",
                              strength=0.6, side="support"), "Asia low"))
        for name, label, tf in (("composite", "Comp", "D1"), ("prior_day", "PD", "D1")):
            p = ctx.vp.get(name)
            if not p:
                continue
            for k in ("poc", "vah", "val"):
                out.append((Level(kind=f"{name}_{k}", low=p[k], high=p[k], timeframe=tf,
                                  strength=0.7 if k == "poc" else 0.55), f"{label} {k.upper()}"))
            if name == "composite":
                for x in p["hvn"][:4]:
                    out.append((Level(kind="hvn", low=x, high=x, timeframe=tf, strength=0.5), "HVN"))
                for x in p["lvn"][:4]:
                    out.append((Level(kind="lvn", low=x, high=x, timeframe=tf, strength=0.35), "LVN"))
        wv = ctx.vwap.get("weekly")
        if wv:
            for k, lab in (("vwap", "wVWAP"), ("u1", "wVWAP+1σ"), ("l1", "wVWAP-1σ"), ("u2", "wVWAP+2σ"),
                           ("l2", "wVWAP-2σ")):
                v = float(wv[k][-1])
                out.append((Level(kind=f"wvwap_{k}", low=v, high=v, timeframe="W1",
                                  strength=0.6 if k == "vwap" else 0.45), lab))
        price = ctx.price
        if price:
            near = sorted(ctx.round_levels, key=lambda lv: abs(lv.mid - price))[:6]
            for r in near:
                out.append((r, f"round {r.mid:g}"))
        return out

    # ------------------------------------------------------------------ clustering & scoring
    def build_map(self, ctx: SymbolContext) -> list[dict]:
        price = ctx.price
        h1_atr = ctx.atr("H1") or ctx.atr("M15")
        if not price or not h1_atr:
            return []
        tol = h1_atr * float(self.lc["cluster_atr_mult"])
        span = (ctx.adr or h1_atr * 8) * 2.0
        cands = [(lv, lab) for lv, lab in self._candidates(ctx) if abs(lv.mid - price) <= span]
        cands.sort(key=lambda x: x[0].low)
        clusters: list[list[tuple[Level, str]]] = []
        for lv, lab in cands:
            if clusters:
                cl = clusters[-1]
                hi = max(x[0].high for x in cl)
                lo = min(x[0].low for x in cl)
                if lv.low - hi <= tol and max(hi, lv.high) - lo <= 3 * tol:
                    cl.append((lv, lab))
                    continue
            clusters.append([(lv, lab)])
        m15 = ctx.tf.get("M15")
        out = []
        for cl in clusters:
            lo = min(x[0].low for x in cl)
            hi = max(x[0].high for x in cl)
            if hi - lo < tol * 0.2:
                pad = tol * 0.15
                lo, hi = lo - pad, hi + pad
            labels = sorted({lab for _, lab in cl})
            kinds = {lv.kind for lv, _ in cl}
            n_src = len(kinds)
            conf = min(1.0, (n_src - 1) / 4.0)
            tfw = max(TF_WEIGHT.get(lv.timeframe, 0.5) for lv, _ in cl)
            fresh = sum(1 for lv, _ in cl if lv.fresh) / len(cl)
            react = self._reaction_volume(m15, lo, hi) if ctx.has_volume else None
            zone_react = max((lv.reaction_volume for lv, _ in cl), default=0.0)
            rv = max(react or 0.0, zone_react)
            react_score = min(1.0, rv / 2.5) if (ctx.has_volume and rv) else 0.4
            mid = (lo + hi) / 2
            dist = 0.0 if lo <= price <= hi else min(abs(price - lo), abs(price - hi))
            dist_score = max(0.0, 1.0 - dist / ((ctx.adr or h1_atr * 8) * 1.5))
            quality = float(np.mean([lv.strength for lv, _ in cl]))
            score = 30 * conf + 20 * tfw + 15 * fresh + 15 * react_score + 10 * dist_score + 10 * quality
            if n_src == 1 and all(lv.kind == "round" for lv, _ in cl):
                score *= 0.6
            role = "support" if price > hi else ("resistance" if price < lo else "at price")
            reversal_kinds = {"demand", "supply", "eqh", "eql", "pdh", "pdl", "pwh", "pwl", "asia_high", "asia_low", "sr"}
            expected = "reversal" if kinds & reversal_kinds else "breakout-retest"
            if ctx.regime.get("regime") in ("trend", "expansion") and not kinds & {"demand", "supply"}:
                expected = "breakout-retest"
            lid = f"{ctx.symbol}:{round(mid / max(tol, 1e-9))}"
            out.append({"id": lid, "symbol": ctx.symbol, "low": lo, "high": hi, "mid": mid, "sources": labels,
                        "kinds": sorted(kinds), "n_sources": n_src, "score": round(score, 1),
                        "grade": grade_for(score, self.lc["grade_thresholds"]), "role": role, "expected": expected,
                        "distance": dist, "distance_atr": dist / h1_atr, "reaction_rvol": round(rv, 2) if rv else None,
                        "tf_weight": tfw})
        return out

    @staticmethod
    def _reaction_volume(tfd, lo: float, hi: float) -> float | None:
        if tfd is None or tfd.rvol is None or tfd.n == 0:
            return None
        h = tfd.bars["high"].to_numpy(float)
        low = tfd.bars["low"].to_numpy(float)
        touch = (low <= hi) & (h >= lo)
        if not touch.any():
            return None
        rv = np.nan_to_num(tfd.rvol[touch], nan=0.0)
        return float(rv.max()) if len(rv) else None

    # ------------------------------------------------------------------ state machine
    def update(self, ctx: SymbolContext) -> list[dict]:
        sym = ctx.symbol
        now = ctx.now
        day = broker_day_start(now, self.cfg.get("system.day_boundary"))
        lv_map = self.build_map(ctx)
        if self.built_day.get(sym) != day and lv_map:
            self.built_day[sym] = day
            top = sorted(lv_map, key=lambda x: -x["score"])[:5]
            self.log("LEVEL", f"{sym} daily level map built: {len(lv_map)} zones, top "
                     + ", ".join(f"{x['mid']:.5g} {x['grade']}" for x in top), sym)
        states = self.states.setdefault(sym, {})
        m5 = ctx.tf.get("M5")
        atr5 = ctx.atr("M5") or 0.0
        atr15 = ctx.atr("M15") or atr5 * 2
        watch = float(self.lc["watch_atr_mult"]) * atr15
        price = ctx.price
        new_states: dict[str, dict] = {}
        for lv in lv_map:
            st = self._match_state(states, lv)
            if st is None:
                st = {"state": "WAITING", "since": now, "entered": None, "role": lv["role"], "reactions": [],
                      "flipped": False, "ready_until": 0.0, "last_bar": 0}
            self._advance(ctx, lv, st, m5, atr5, watch, price, now)
            lv.update({"state": st["state"], "state_since": st["since"], "reactions": st["reactions"][-4:],
                       "countdown": st.get("countdown"), "role": st["role"] if lv["role"] == "at price" else lv["role"],
                       "flipped": st["flipped"]})
            if st["state"] == "READY":
                lv["score"] = min(100.0, lv["score"] + 5)
            if st["flipped"]:
                lv["expected"] = "breakout-retest"
            new_states[lv["id"]] = st
        self.states[sym] = new_states
        if price:
            above = sorted([x for x in lv_map if x["mid"] >= price], key=lambda x: x["mid"])
            below = sorted([x for x in lv_map if x["mid"] < price], key=lambda x: -x["mid"])
            n = int(self.lc["top_n"])
            above = sorted(above[: n * 2], key=lambda x: -x["score"])[:n]
            below = sorted(below[: n * 2], key=lambda x: -x["score"])[:n]
            lv_map_top = sorted(above, key=lambda x: -x["mid"]) + sorted(below, key=lambda x: -x["mid"])
        else:
            lv_map_top = lv_map
        self.maps[sym] = lv_map
        ready = [x for x in lv_map if x["state"] == "READY"]
        self.set_status(f"{sym}: {len(lv_map)} zones · {len(ready)} READY · "
                        f"{sum(1 for x in lv_map if x['state'] == 'WATCHING')} watching")
        self.pulse(len(ready))
        ctx.levels_top = lv_map_top  # type: ignore[attr-defined]
        return lv_map

    @staticmethod
    def _match_state(states: dict, lv: dict) -> dict | None:
        if lv["id"] in states:
            return states[lv["id"]]
        for sid, st in states.items():
            if st.get("low") is not None and not (lv["high"] < st["low"] or lv["low"] > st["high"]):
                return st
        return None

    def _advance(self, ctx, lv, st, m5, atr5, watch, price, now) -> None:
        st["low"], st["high"] = lv["low"], lv["high"]
        if price is None or m5 is None or m5.n < 5 or atr5 <= 0:
            return
        bars = m5.bars
        last_t = int(bars["time"].iloc[-1])
        lo, hi = lv["low"], lv["high"]
        role = st["role"] if st["role"] in ("support", "resistance") else lv["role"]
        if role == "at price":
            role = "support" if bars["close"].iloc[-6] >= hi else "resistance"
        st["role"] = role
        dist = 0.0 if lo <= price <= hi else min(abs(price - lo), abs(price - hi))
        prev_state = st["state"]
        new_bar = last_t != st.get("last_bar")
        st["last_bar"] = last_t
        sym = ctx.symbol
        # ---- BROKEN check (2+ closes beyond the far edge + volume)
        nb = int(self.lc["broken_closes"])
        closes = bars["close"].to_numpy(float)[-nb:]
        beyond = (closes < lo).all() if role == "support" else (closes > hi).all()
        vol_ok = True
        if ctx.has_volume and m5.rvol is not None:
            rv = np.nan_to_num(m5.rvol[-nb:], nan=0.0)
            vol_ok = float(rv.mean()) >= float(self.lc["broken_rvol"])
        if new_bar and beyond and vol_ok and st["state"] != "BROKEN":
            st["state"] = "BROKEN"
            st["since"] = now
            st["flipped"] = True
            st["role"] = "resistance" if role == "support" else "support"
            st["entered"] = None
            self.log("LEVEL", f"BROKEN {sym} {lv['mid']:.5g} {lv['grade']} {role} -> role flip to {st['role']}", sym,
                     {"level": lv["id"]})
            return
        if st["state"] == "BROKEN":
            if dist > 3 * watch:
                st["state"], st["since"] = "WAITING", now
            return
        touched_now = bars["low"].iloc[-1] <= hi and bars["high"].iloc[-1] >= lo
        in_zone = lo <= price <= hi or touched_now
        if in_zone and st["entered"] is None and st["state"] not in ("STALLED",):
            st["entered"] = last_t
            st["reactions"] = []
        if st["entered"] is not None and new_bar and st["state"] not in ("READY", "STALLED"):
            seg = bars[bars["time"] >= st["entered"]]
            reacts = self._reactions(ctx, m5, seg, lo, hi, role, atr5)
            st["reactions"] = reacts
            n_bars = len(seg)
            limit = int(self.tt["reaction_bars"])
            if reacts:
                st["state"], st["since"] = "READY", now
                st["ready_until"] = now + int(self.lc["ready_valid_bars"]) * 300
                st["countdown"] = None
                self.log("LEVEL", f"READY {sym} {lv['mid']:.5g} {lv['grade']} {role}: {' + '.join(reacts)}", sym,
                         {"level": lv["id"]})
                return
            if self.tt["enabled"] and n_bars >= limit:
                st["state"], st["since"] = "STALLED", now
                st["countdown"] = None
                self.log("LEVEL", f"STALLED {sym} {lv['mid']:.5g} {lv['grade']}: no valid reaction within "
                         f"{limit} M5 bars", sym, {"level": lv["id"]})
                return
            st["countdown"] = {"bars_left": max(0, limit - n_bars),
                               "deadline": float(st["entered"]) + limit * 300}
        if st["state"] == "READY" and now > st["ready_until"]:
            st["state"], st["since"], st["entered"] = ("WATCHING" if dist <= watch else "WAITING"), now, None
            return
        if st["state"] == "STALLED":
            if dist > watch:
                st["state"], st["since"], st["entered"] = "WAITING", now, None
            return
        if st["state"] == "READY":
            return
        if st["entered"] is not None and not in_zone and dist > watch:
            st["entered"] = None
        target = "WATCHING" if dist <= watch else "WAITING"
        if target != st["state"]:
            st["state"], st["since"] = target, now
            if target == "WATCHING" and prev_state == "WAITING" and lv["grade"] in ("A+", "A"):
                self.log("LEVEL", f"WATCHING {sym} {lv['mid']:.5g} {lv['grade']} {role} "
                         f"({dist / max(atr5, 1e-9):.1f} M5-ATR away)", sym, {"level": lv["id"]})

    def _reactions(self, ctx, m5, seg, lo, hi, role, atr5) -> list[str]:
        """Valid live reaction signals since the zone was entered."""
        if len(seg) == 0:
            return []
        direction = "long" if role == "support" else "short"
        out = []
        last = seg.iloc[-1]
        n = m5.n
        idx0 = n - len(seg)
        for i in range(len(seg)):
            r = seg.iloc[i]
            if candles.wick_ratio(seg, i, direction) >= float(self.lc["ready_wick_ratio"]):
                out.append("rejection wick")
                break
        if direction == "long" and seg["low"].min() < lo and last.close > lo:
            out.append("sweep-and-reclaim")
        if direction == "short" and seg["high"].max() > hi and last.close < hi:
            out.append("sweep-and-reclaim")
        if ctx.has_volume and m5.rvol is not None:
            rv = np.nan_to_num(m5.rvol[idx0:], nan=0.0)
            cl = np.array([candles.close_location(seg, i) for i in range(len(seg))])
            good = (rv >= float(self.lc["ready_rvol"])) & ((cl > 0.3) if direction == "long" else (cl < -0.3))
            if good.any():
                out.append(f"RVOL {rv.max():.1f}")
            if m5.flags.get("absorption") is not None and m5.flags["absorption"][idx0:].any():
                out.append("absorption")
            if m5.delta is not None and len(seg) >= 2:
                d = m5.delta[idx0 - 1:] if idx0 > 0 else m5.delta[idx0:]
                if len(d) >= 2 and ((d[-1] > 0 > d[0]) if direction == "long" else (d[-1] < 0 < d[0])):
                    out.append("delta shift")
        move = (last.close - seg["low"].min()) if direction == "long" else (seg["high"].max() - last.close)
        if move >= float(self.tt["reaction_min_atr_move"]) * atr5 and out:
            out.append(f"{move / atr5:.1f}xATR move")
        return out

    def level_for(self, symbol: str, price_lo: float, price_hi: float) -> dict | None:
        for lv in self.maps.get(symbol, []):
            if not (price_hi < lv["low"] or price_lo > lv["high"]):
                return lv
        return None

    def opposing(self, symbol: str, direction: str, entry: float, min_grade: str = "B") -> list[dict]:
        order = {"A+": 3, "A": 2, "B": 1, "C": 0}
        out = []
        for lv in self.maps.get(symbol, []):
            if order[lv["grade"]] < order[min_grade]:
                continue
            if direction == "long" and lv["low"] > entry:
                out.append(lv)
            if direction == "short" and lv["high"] < entry:
                out.append(lv)
        out.sort(key=lambda x: (x["low"] - entry) if direction == "long" else (entry - x["high"]))
        return out


class DxyAnalyzer:
    """DXY state: broker symbol if present, else synthetic ICE formula from the feed's FX pairs."""

    def __init__(self, cfg, feed, bus):
        self.cfg = cfg
        self.dc = cfg.get("dxy")
        self.feed = feed
        self.bus = bus
        self.state: dict = {"available": False, "reason": "not computed yet"}
        self._last_key = None
        self.bars_m15 = None
        self.bars_m5 = None

    def _broker_symbol(self) -> str | None:
        for name in self.dc["broker_symbols"]:
            src = self.feed.source_for(name)
            if src is not None and src.kind == "mt5":
                return name
        return None

    def _bars(self, tf: str, count: int):
        b = self._broker_symbol()
        if b:
            return self.feed.bars(b, tf, count), [], f"broker {b}"
        comps = {p: self.feed.bars(p, tf, count) for p in dxym.ICE_WEIGHTS}
        bars, missing = dxym.synthetic_bars(comps)
        label = "synthetic ICE" + (f" (partial: missing {', '.join(missing)})" if missing else "")
        return bars, missing, label

    def update(self, contexts: dict[str, SymbolContext]) -> dict:
        now = self.feed.now()
        key = int(now // 300)
        if key == self._last_key and self.state.get("available"):
            self._update_correlations(contexts)
            return self.state
        self._last_key = key
        m15, missing, label = self._bars("M15", 400)
        m5, _, _ = self._bars("M5", 300)
        if m15 is None or len(m15) < 50:
            self.state = {"available": False, "reason": f"DXY n/a ({label}; {missing})", "label": label}
            return self.state
        from aurum_nexus.data.bars import closed_only, resample
        m15 = closed_only(m15, "M15", now)
        m5 = closed_only(m5, "M5", now) if m5 is not None and len(m5) else m5
        self.bars_m15, self.bars_m5 = m15, m5
        st15 = structure.analyze(m15, 3)
        h1 = resample(m15, "H1")
        st1 = structure.analyze(h1, 2)
        a15 = atrm.atr_last(m15) or 0
        a5 = atrm.atr_last(m5) if m5 is not None and len(m5) > 20 else None
        bias = st15["trend"]
        if st1["trend"] != "neutral" and st1["trend"] != bias:
            bias = "neutral"
        day_start = broker_day_start(now, self.cfg.get("system.day_boundary"))
        dv = vwm.anchored(m5 if m5 is not None and len(m5) else m15, day_start)
        price = float(m15["close"].iloc[-1])
        below_vwap = (price < dv["last"]) if dv else None
        pivots = levels_basic.pivot_clusters(st15["swings"], 0.35 * a15, "M15") if a15 else []
        dw = levels_basic.daily_weekly(m15)
        res_levels = [p.mid for p in pivots if p.mid > price] + [dw[k] for k in ("pdh", "pwh") if dw.get(k)]
        sup_levels = [p.mid for p in pivots if p.mid < price] + [dw[k] for k in ("pdl", "pwl") if dw.get(k)]
        rej_res = rej_sup = False
        if m5 is not None and len(m5) > 5 and a5:
            seg = m5.tail(3)
            for i in range(len(seg)):
                hi, lo = float(seg["high"].iloc[i]), float(seg["low"].iloc[i])
                if any(abs(hi - r) <= 0.3 * a15 for r in res_levels) and candles.wick_ratio(seg, i, "short") >= 0.5:
                    rej_res = True
                if any(abs(lo - s) <= 0.3 * a15 for s in sup_levels) and candles.wick_ratio(seg, i, "long") >= 0.5:
                    rej_sup = True
        n15 = len(m15)
        brk_up = structure.recent_event(st15, 3, n15, direction="bull") is not None
        brk_dn = structure.recent_event(st15, 3, n15, direction="bear") is not None
        shock = None
        if m5 is not None and len(m5) > 20 and a5:
            last = m5.iloc[-1]
            rng = float(last.high - last.low)
            if rng >= float(self.dc["shock_atr_mult"]) * a5:
                shock = {"direction": "up" if last.close > last.open else "down", "time": int(last.time),
                         "atr_mult": round(rng / a5, 1)}
                self.bus.emit("LEVEL", "LEVELS", f"DXY SHOCK {shock['direction']} {shock['atr_mult']}x ATR(M5)", "DXY")
        self.state = {
            "available": True, "label": label, "missing": missing, "value": price, "bias": bias,
            "trend_m15": st15["trend"], "trend_h1": st1["trend"], "below_vwap": below_vwap,
            "vwap": dv["last"] if dv else None, "vwap_basis": dv["basis"] if dv else None,
            "rejecting_resistance": rej_res, "rejecting_support": rej_sup, "breaking_up": brk_up,
            "breaking_down": brk_dn, "shock": shock, "time": int(m15["time"].iloc[-1]),
            "resistance": sorted(res_levels)[:3], "support": sorted(sup_levels, reverse=True)[:3],
            "events": [{"kind": e.kind, "direction": e.direction, "time": e.time, "price": e.price}
                       for e in st15["events"][-6:]],
            "correlations": {}, "divergence": {}, "partial": bool(missing),
        }
        self._st15 = st15
        self._update_correlations(contexts)
        return self.state

    def _update_correlations(self, contexts: dict[str, SymbolContext]) -> None:
        if not self.state.get("available") or self.bars_m15 is None:
            return
        n = int(self.dc["corr_bars"])
        corrs, divs = {}, {}
        for sym, ctx in contexts.items():
            rel = DXY_RELATION.get(sym, 0)
            m15 = ctx.bars("M15")
            if m15 is None or len(m15) < 40:
                continue
            c = dxym.returns_correlation(m15, self.bars_m15, n)
            corrs[sym] = round(c, 3) if c is not None else None
            if rel != 0:
                d = self._smt(ctx, rel)
                if d:
                    divs[sym] = d
        self.state["correlations"] = corrs
        self.state["divergence"] = divs
        for sym, c in corrs.items():
            if c is not None and abs(c) < float(self.dc["decoupled_abs_corr"]) and DXY_RELATION.get(sym):
                self.state.setdefault("decoupled", [])
                if sym not in self.state["decoupled"]:
                    self.state["decoupled"].append(sym)

    def _smt(self, ctx: SymbolContext, rel: int) -> str | None:
        """SMT-style divergence between symbol and DXY over the last ~40 M15 bars."""
        st_s = ctx.tf["M15"].structure
        st_d = getattr(self, "_st15", None)
        if not st_s or not st_d:
            return None
        n_s, n_d = ctx.tf["M15"].n, len(self.bars_m15)

        def last2(st, kind, n):
            pts = [s for s in st["swings"] if s.kind == kind and n - s.idx <= 40]
            return pts[-2:] if len(pts) >= 2 else None

        sl, sh = last2(st_s, "L", n_s), last2(st_s, "H", n_s)
        dl, dh = last2(st_d, "L", n_d), last2(st_d, "H", n_d)
        if rel < 0:
            if sl and dh and sl[1].price < sl[0].price and dh[1].price <= dh[0].price:
                return "bullish"
            if sh and dl and sh[1].price > sh[0].price and dl[1].price >= dl[0].price:
                return "bearish"
        else:
            if sl and dl and sl[1].price < sl[0].price and dl[1].price >= dl[0].price:
                return "bullish"
            if sh and dh and sh[1].price > sh[0].price and dh[1].price <= dh[0].price:
                return "bearish"
        return None

    def snapshot(self, limit: int = 200) -> dict:
        out = {k: v for k, v in self.state.items()}
        if self.bars_m15 is not None and len(self.bars_m15):
            b = self.bars_m15.tail(limit)
            out["bars"] = [{"time": int(r.time), "open": float(r.open), "high": float(r.high), "low": float(r.low),
                            "close": float(r.close)} for r in b.itertuples(index=False)]
        return out
