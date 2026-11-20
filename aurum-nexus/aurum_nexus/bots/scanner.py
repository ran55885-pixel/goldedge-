"""SCANNER - market-structure analyst.

HTF (D1/H4/H1) and LTF (M15/M5/M1) are all scanned: swings, HH/HL/LH/LL, BOS/CHoCH, premium/discount,
supply/demand zones (closed bars only), pivot S/R clusters, liquidity pools, FVGs, order blocks,
order-flow estimates (delta, CVD, RVOL, volume flags, CVD divergences). Plus PDH/PDL/PWH/PWL,
daily/weekly open, Asian range, Volume Profiles (session, daily, prior-day, composite, weekly),
VWAP daily/weekly/anchored with ±1σ/±2σ, ATR regime, ADR and ADR-used %, market regime.
Each timeframe is recomputed only when a new bar of that timeframe has closed.
"""
from __future__ import annotations

import numpy as np

from aurum_nexus.bots.base import Bot
from aurum_nexus.context import SymbolContext, TFData
from aurum_nexus.indicators import atr as atrm
from aurum_nexus.indicators import fvg_ob, levels_basic, liquidity, orderflow, regime, structure
from aurum_nexus.indicators import volume_profile as vpm
from aurum_nexus.indicators import vwap as vwm
from aurum_nexus.indicators.zones import find_zones
from aurum_nexus.models import TF_SECONDS
from aurum_nexus.timeutil import broker_day_ids, current_session, window_bounds

TF_ORDER = ["D1", "H4", "H1", "M15", "M5", "M1"]


class Scanner(Bot):
    name = "SCANNER"
    color = "#00e5ff"
    job = "Market structure, zones, liquidity, volume profile & VWAP on D1→M1"

    def __init__(self, bus, cfg, feed, clock=None):
        super().__init__(bus, cfg, clock or feed.now)
        self.feed = feed
        self.contexts: dict[str, SymbolContext] = {}
        sc = cfg.get("scanner")
        self.tfs = [tf for tf in TF_ORDER if tf in (sc["htf"] + sc["ltf"])]
        for need in ("M1", "M5", "M15", "H1", "D1"):
            if need not in self.tfs:
                self.tfs.append(need)
        self.tfs = [tf for tf in TF_ORDER if tf in self.tfs]
        self.sc = sc
        self.day_boundary = cfg.get("system.day_boundary")
        self.sessions = cfg.get("sessions")

    def config_view(self) -> dict:
        return {"timeframes": self.tfs, "zone": self.sc["zone"], "vp_rows": self.sc["vp_rows"],
                "value_area_pct": self.sc["value_area_pct"]}

    # ------------------------------------------------------------------ per timeframe
    def _tf_analysis(self, symbol: str, tf: str, bars, has_vol: bool, m1=None) -> TFData:
        sc = self.sc
        d = TFData(tf=tf, bars=bars)
        if bars is not None and len(bars):
            d.last_bar_time = int(bars["time"].iloc[-1])
        if bars is None or len(bars) < 20:
            return d
        h, low, c = bars["high"].to_numpy(float), bars["low"].to_numpy(float), bars["close"].to_numpy(float)
        a = atrm.atr(h, low, c, sc["atr_period"])
        d.atr_series = a
        d.atr = float(a[-1]) if np.isfinite(a[-1]) and a[-1] > 0 else None
        k = int(sc["swing_lookback"].get(tf, 3))
        d.structure = structure.analyze(bars, k)
        z = sc["zone"]
        d.zones = find_zones(bars, tf, z["impulse_atr_mult"], z["impulse_body_ratio"], z["base_max_bars"],
                             z["base_max_range_atr"], z["max_touches"], z["max_zones"])
        d.fvgs = fvg_ob.find_fvgs(bars, tf)
        d.obs = fvg_ob.find_order_blocks(bars, d.structure.get("events", []), tf)
        if d.atr:
            d.liquidity = liquidity.equal_levels(d.structure["swings"], bars, sc["equal_level_atr_mult"] * d.atr, tf)
            d.pivots = levels_basic.pivot_clusters(d.structure["swings"], sc["pivot_cluster_atr_mult"] * d.atr, tf)
        if has_vol and tf in ("M1", "M5", "M15", "H1"):
            if self.cfg.get("signal.delta_method") == "m1_updown" and m1 is not None and tf != "M1":
                d.delta = orderflow.delta_from_m1(bars, m1, TF_SECONDS[tf])
            else:
                d.delta = orderflow.delta_clv(bars)
            day_ids = broker_day_ids(bars["time"].to_numpy("int64"), self.day_boundary)
            d.cvd = orderflow.cvd(d.delta, day_ids)
            d.rvol = orderflow.rvol(bars, int(self.cfg.get("signal.rvol_days", 10)))
            d.flags = orderflow.flags(bars, d.rvol)
            d.divergences = orderflow.divergences(bars, d.structure["swings"], d.cvd)
        d.last_bar_time = int(bars["time"].iloc[-1])
        return d

    # ------------------------------------------------------------------ main update
    def update(self, symbol: str) -> SymbolContext:
        now = self.feed.now()
        ctx = self.contexts.get(symbol) or SymbolContext(symbol=symbol, now=now)
        ctx.now = now
        ctx.tick = self.feed.tick(symbol)
        q = self.feed.quote_status(symbol)
        ctx.source, ctx.source_label, ctx.preview = q.get("source", ""), q.get("label", ""), bool(q.get("preview"))
        ctx.has_volume = self.feed.has_volume(symbol)
        if ctx.tick:
            ctx.spread = ctx.tick.spread
            ctx.spread_hist = (ctx.spread_hist + [ctx.tick.spread])[-300:]
        changed = []
        m1 = None
        for tf in reversed(self.tfs):  # M1 first so it can feed the delta estimate
            bars = self.feed.bars(symbol, tf, int(self.sc["bars"].get(tf, 500)))
            if tf == "M1":
                m1 = bars
            if bars is None or len(bars) == 0:
                if tf not in ctx.tf:
                    ctx.tf[tf] = TFData(tf=tf, bars=bars)
                continue
            last = int(bars["time"].iloc[-1])
            prev = ctx.tf.get(tf)
            if prev is not None and prev.last_bar_time == last and prev.n == len(bars):
                continue
            try:
                ctx.tf[tf] = self._tf_analysis(symbol, tf, bars, ctx.has_volume, m1)
                changed.append(tf)
            except Exception as e:  # never let one TF break the cycle
                ctx.errors = (ctx.errors + [f"{tf}: {e}"])[-10:]
                self.log("ERROR", f"{symbol} {tf} analysis failed: {e}", symbol)
        if any(tf != "M1" for tf in changed) or (changed and not ctx.daily):
            self._daily_context(ctx)
        elif changed:
            self._intraday_context(ctx)
        ctx.session = current_session(now, self.sessions)
        ctx.updated = now
        self.contexts[symbol] = ctx
        if changed:
            self.set_status(f"{symbol}: updated {','.join(changed)} · regime {ctx.regime.get('regime', 'n/a')}")
            self.pulse(len(changed))
        return ctx

    def _daily_context(self, ctx: SymbolContext) -> None:
        sc = self.sc
        h1 = ctx.bars("H1")
        m1 = ctx.bars("M1")
        m5 = ctx.bars("M5")
        m15 = ctx.bars("M15")
        d1 = ctx.bars("D1")
        now = ctx.now
        base = m15 if m15 is not None and len(m15) else h1
        if base is not None and len(base):
            ctx.daily = levels_basic.daily_weekly(base, self.day_boundary)
            if h1 is not None and len(h1) and len(h1) > len(base) // 4:
                dw = levels_basic.daily_weekly(h1, self.day_boundary)
                for k in ("pwh", "pwl", "pdh", "pdl"):
                    ctx.daily.setdefault(k, dw.get(k))
        ctx.asia = levels_basic.asian_range(m5 if m5 is not None and len(m5) else base, now, self.sessions["asia_range"])
        ctx.adr = atrm.adr(d1, sc["adr_days"]) if d1 is not None else None
        if ctx.daily.get("today_high") is not None:
            ctx.adr_used = atrm.adr_used_pct(ctx.daily["today_high"], ctx.daily["today_low"], ctx.adr)
        h1d = ctx.tf.get("H1")
        if h1d is not None and h1d.atr_series is not None:
            ctx.atr_pct = atrm.atr_percentile(h1d.atr_series)
            ctx.atr_regime = atrm.atr_regime(ctx.atr_pct)
        self._intraday_context(ctx)
        # Volume profiles
        rows, va = int(sc["vp_rows"]), float(sc["value_area_pct"])
        ds = ctx.daily.get("day_start")
        ws = ctx.daily.get("week_start")
        if m5 is not None and len(m5) and ds:
            ctx.vp["daily"] = vpm.profile(vpm.segment(m5, ds), rows, va, ctx.has_volume)
            ctx.vp["prior_day"] = vpm.profile(vpm.segment(m5, ds - 86400, ds), rows, va, ctx.has_volume)
        if m15 is not None and len(m15):
            ctx.vp["composite"] = vpm.profile(m15.tail(5 * 96), rows, va, ctx.has_volume)
            if ws:
                ctx.vp["weekly"] = vpm.profile(vpm.segment(m15, ws), rows, va, ctx.has_volume)
        # VWAPs
        if m15 is not None and len(m15) and ws:
            ctx.vwap["weekly"] = vwm.anchored(m15, ws)
        h1s = (h1d.structure if h1d else {}) or {}
        last_sw = None
        for s in reversed(h1s.get("swings", [])):
            last_sw = s
            break
        if last_sw is not None and m5 is not None and len(m5):
            ctx.vwap["anchored"] = vwm.anchored(m5, last_sw.time)
            if ctx.vwap["anchored"]:
                ctx.vwap["anchored"]["anchor_label"] = f"H1 swing {last_sw.label}"
        # round numbers
        if ctx.price:
            ctx.round_levels = levels_basic.round_levels(ctx.price, ctx.adr, self.cfg.get("symbols.levels_round_steps"))
        # regime
        st = h1s
        events = st.get("events", [])[-12:]
        bos = sum(1 for e in events if e.kind == "BOS")
        choch = sum(1 for e in events if e.kind == "CHoCH")
        comp = ctx.vp.get("composite") or {}
        ctx.regime = regime.classify(st.get("trend", "neutral"), bos, choch, ctx.atr_pct, ctx.adr_used,
                                     comp.get("balanced") if comp else None)
        ctx.htf_bias = self._htf_bias(ctx)

    def _intraday_context(self, ctx: SymbolContext) -> None:
        """Parts that change every M1 bar: session profile, daily VWAP, HTF bias acceptance."""
        m1 = ctx.bars("M1")
        if m1 is None or len(m1) == 0:
            return
        rows, va = int(self.sc["vp_rows"]), float(self.sc["value_area_pct"])
        sess_win = self._session_window(ctx.now)
        if sess_win:
            ctx.vp["session"] = vpm.profile(vpm.segment(m1, sess_win[0], sess_win[1]), rows, va, ctx.has_volume)
            ctx.vp["session_name"] = sess_win[2]
        ds = (ctx.daily or {}).get("day_start")
        if ds:
            ctx.vwap["daily"] = vwm.anchored(m1, ds)
        if ctx.daily:
            ctx.htf_bias = self._htf_bias(ctx)

    def _session_window(self, now: float):
        for name in ("london", "newyork", "asia"):
            w = self.sessions[name]
            s, e = window_bounds(now, w)
            if s <= now < e:
                return s, e, name
        return None

    def _htf_bias(self, ctx: SymbolContext) -> dict:
        """HTF bias, MA-free: structure + premium/discount + weekly VWAP + composite POC +
        daily VWAP acceptance. Returns votes and a net bias."""
        votes = {}
        for tf in ("H4", "H1"):
            d = ctx.tf.get(tf)
            if d and d.structure:
                t = d.structure.get("trend")
                votes[f"{tf} structure"] = 1 if t == "bull" else (-1 if t == "bear" else 0)
        h1 = ctx.tf.get("H1")
        if h1 and h1.structure.get("zone") in ("premium", "discount"):
            votes["H1 premium/discount"] = -1 if h1.structure["zone"] == "premium" else 1
        p = ctx.price
        wv = ctx.vwap.get("weekly")
        if p and wv:
            votes["weekly VWAP"] = 1 if p > wv["last"] else -1
        comp = ctx.vp.get("composite")
        if p and comp:
            votes["composite POC"] = 1 if p > comp["poc"] else -1
        dv = ctx.vwap.get("daily")
        m5 = ctx.bars("M5")
        if dv is not None and m5 is not None:
            acc = vwm.acceptance(m5, dv)
            votes["daily VWAP acceptance"] = 1 if acc == "above" else (-1 if acc == "below" else 0)
        net = sum(votes.values())
        n = max(1, len(votes))
        bias = "bull" if net >= 2 else ("bear" if net <= -2 else "neutral")
        return {"bias": bias, "net": net, "n": n, "votes": votes, "strength": abs(net) / n}
