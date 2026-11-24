"""SIGNAL - execution trader on M1, M5 and M15 simultaneously.

Setups (playbooks): zone_reaction, sweep_reversal, bos_retest, fvg_ob, range_breakout, orb (London/NY
opening range 15/30m), vwap_reversion (±2σ fade), wyckoff (spring/upthrust), london_judas (Asian range
sweep), ny_open_reversal, second_leg (pullback to M15 FVG after BOS), asia_range_reversion (opt-in for
gold, default for JPY/AUD/NZD).
LTF confirmation: CHoCH, engulfing, pin/rejection, micro-BOS, delta on the trigger candle.

Score 0-100 = documented weights (sum 100):
  zone_quality 15 · confluence 15 · confirmation 15 · bias 15 · volume_flow 20 · proximity 5 ·
  stop_quality 10 · conditions 5
plus bounded modifiers: Levels READY (+8 max / STALLED -8), DXY (±10), regime (±5), sentiment/macro (±5),
seasonality (±5), Analyst adaptive setup weight (±15); total modifier capped at ±25.
Hard rules here: RR to TP1/final TP/next obstacle >= min_rr (never stretch TP), ADR exhaustion filter,
de-duplication across timeframes and zones.
"""
from __future__ import annotations

import numpy as np

from aurum_nexus.bots.base import Bot
from aurum_nexus.bots.strategist import REVERSAL_SETUPS
from aurum_nexus.context import SymbolContext
from aurum_nexus.dxy import dxy_modifier
from aurum_nexus.indicators import candles, liquidity, structure
from aurum_nexus.models import TF_SECONDS, Candidate
from aurum_nexus.timeutil import in_window, to_dt, window_bounds

LOWER_TF = {"M15": "M5", "M5": "M1", "M1": "M1"}
HIGHER_TFS = {"M1": ["M1", "M5", "M15", "H1", "H4"], "M5": ["M5", "M15", "H1", "H4"], "M15": ["M15", "H1", "H4", "D1"]}
# structure used as a reference by a trigger timeframe must come from a HIGHER timeframe
REF_TFS = {"M1": ["M15", "H1"], "M5": ["M15", "H1", "H4"], "M15": ["H1", "H4", "D1"]}
ZONE_TFS = {"M1": ["M5", "M15", "H1", "H4"], "M5": ["M15", "H1", "H4"], "M15": ["M15", "H1", "H4", "D1"]}
GRADE_READY_BONUS = {"A+": 8, "A": 6, "B": 4, "C": 2}


class SignalBot(Bot):
    name = "SIGNAL"
    color = "#39ff14"
    job = "Setups on M1/M5/M15, LTF confirmation, order-flow scoring"

    def __init__(self, bus, cfg, levels, strategist, analyst=None, intel=None, clock=None):
        super().__init__(bus, cfg, clock)
        self.levels = levels
        self.strategist = strategist
        self.analyst = analyst
        self.intel = intel
        self.sc = cfg.get("signal")
        self.sessions = cfg.get("sessions")
        self.last_eval: dict[tuple[str, str], int] = {}
        self.latest: dict[str, list[Candidate]] = {}
        self.skips: dict[str, list[dict]] = {}
        self._skip_seen: set = set()
        self._orb_used: set = set()

    def config_view(self) -> dict:
        return {"weights": self.sc["weights"], "modifier_caps": self.sc["modifier_caps"],
                "min_score": self.cfg.min_score, "min_rr": self.cfg.min_rr,
                "adr_exhaustion_pct": self.sc["adr_exhaustion_pct"], "entry_timeframes": self.sc["entry_timeframes"]}

    # ------------------------------------------------------------------ helpers
    def _session_ok(self, names: list[str], now: float) -> bool:
        for n in names:
            w = self.sessions.get(n)
            if w and "end" in w and in_window(now, w):
                return True
        return False

    def confirmations(self, ctx: SymbolContext, tf: str, direction: str) -> list[str]:
        out = []
        d = ctx.tf.get(tf)
        if d is None or d.n < 10:
            return out
        dirw = "bull" if direction == "long" else "bear"
        if structure.recent_event(d.structure, 5, d.n, kind="CHoCH", direction=dirw):
            out.append(f"CHoCH {tf}")
        ltf = LOWER_TF[tf]
        ld = ctx.tf.get(ltf)
        if ld is not None and ld.n > 10 and structure.recent_event(ld.structure, 3, ld.n, direction=dirw):
            out.append(f"micro-BOS {ltf}")
        e = candles.engulfing(d.bars)
        if e == dirw:
            out.append("engulfing")
        r = candles.rejection(d.bars)
        if r == dirw:
            out.append("rejection pin")
        if d.delta is not None and len(d.delta) > 20:
            last = d.delta[-1]
            med = float(np.median(np.abs(d.delta[-21:-1]))) or 0.0
            if (last > 0 if direction == "long" else last < 0) and abs(last) >= med:
                out.append("delta")
        return out

    def volume_flow(self, ctx: SymbolContext, tf: str, direction: str) -> tuple[float, list[str]]:
        d = ctx.tf.get(tf)
        if not ctx.has_volume or d is None or d.rvol is None:
            return 0.4, ["volume n/a (source has no volume)"]
        notes = []
        s = 0.0
        rv = float(np.nan_to_num(d.rvol[-1], nan=0.0))
        if rv >= 2.0:
            s += 0.35
            notes.append(f"RVOL {rv:.1f}")
        elif rv >= 1.2:
            s += 0.25
            notes.append(f"RVOL {rv:.1f}")
        if d.delta is not None and ((d.delta[-1] > 0) if direction == "long" else (d.delta[-1] < 0)):
            s += 0.2
            notes.append("delta agrees")
        want = "bullish" if direction == "long" else "bearish"
        if any(dv["type"] == want and d.n - 1 - dv["idx"] <= 10 for dv in d.divergences):
            s += 0.2
            notes.append(f"CVD {want} divergence")
        f = d.flags or {}
        if f.get("absorption") is not None and f["absorption"][-3:].any():
            s += 0.15
            notes.append("absorption")
        key = "no_supply" if direction == "long" else "no_demand"
        if f.get(key) is not None and f[key][-3:].any():
            s += 0.1
            notes.append(key.replace("_", "-"))
        if direction == "long" and f.get("stopping_volume") is not None and f["stopping_volume"][-3:].any():
            s += 0.1
            notes.append("stopping volume")
        return min(1.0, s), notes

    # ------------------------------------------------------------------ setups
    def _zones_for(self, ctx, tf):
        out = []
        for t in ZONE_TFS[tf]:
            d = ctx.tf.get(t)
            if d:
                out.extend(d.zones)
        return out

    def setups(self, ctx: SymbolContext, tf: str, profile: dict) -> list[dict]:
        d = ctx.tf.get(tf)
        if d is None or d.n < 30 or not d.atr:
            return []
        pbs = profile["playbooks"]
        raw: list[dict] = []
        now = ctx.now
        for name, pb in pbs.items():
            if not pb.get("enabled") or tf not in pb.get("timeframes", [tf]):
                continue
            if not self._session_ok(pb.get("sessions", []), now):
                continue
            fn = getattr(self, f"_s_{name}", None)
            if fn is None:
                continue
            try:
                for r in fn(ctx, tf, d, profile) or []:
                    r.setdefault("setup", name)
                    r.setdefault("tf", tf)
                    r.setdefault("entry_type", pb.get("entry", "market"))
                    raw.append(r)
            except Exception as e:
                self.log("ERROR", f"{ctx.symbol} {name} {tf} failed: {e}", ctx.symbol)
        return raw

    def _s_zone_reaction(self, ctx, tf, d, profile):
        out = []
        b = d.bars
        last = b.iloc[-1]
        atr = d.atr
        for z in self._zones_for(ctx, tf):
            if z.kind == "demand" and min(b["low"].iloc[-2:]) <= z.high and last.close > z.low:
                if self.confirmations(ctx, tf, "long"):
                    out.append(self._zone_raw(z, "long", last, atr, b))
            elif z.kind == "supply" and max(b["high"].iloc[-2:]) >= z.low and last.close < z.high:
                if self.confirmations(ctx, tf, "short"):
                    out.append(self._zone_raw(z, "short", last, atr, b))
        for lv in self.levels.maps.get(ctx.symbol, []):
            if lv.get("state") != "READY" or lv.get("expected") != "reversal":
                continue
            direction = "long" if lv["role"] == "support" else "short"
            if self.confirmations(ctx, tf, direction):
                anchor = min(lv["low"], float(b["low"].iloc[-3:].min())) if direction == "long" else \
                    max(lv["high"], float(b["high"].iloc[-3:].max()))
                out.append({"direction": direction, "anchor": anchor, "zone": (lv["low"], lv["high"]),
                            "zone_id": lv["id"], "zone_quality": lv["score"] / 100.0,
                            "notes": [f"Levels READY {lv['grade']}"], "edge": lv["high"] if direction == "long" else lv["low"]})
        return out

    @staticmethod
    def _zone_raw(z, direction, last, atr, b):
        if direction == "long":
            anchor = min(z.low, float(b["low"].iloc[-3:].min()))
            edge = z.high
        else:
            anchor = max(z.high, float(b["high"].iloc[-3:].max()))
            edge = z.low
        q = z.strength + (0.1 if z.timeframe in ("H1", "H4", "D1") else 0.0)
        return {"direction": direction, "anchor": anchor, "zone": (z.low, z.high),
                "zone_id": f"{z.kind}:{z.timeframe}:{z.created}", "zone_quality": min(1.0, q),
                "notes": [f"{z.timeframe} {z.kind} (touches {z.touches})"], "edge": edge}

    def _liquidity_targets(self, ctx, tf):
        out = []
        dl = ctx.daily or {}
        for k, imp in (("pwh", 0.9), ("pdh", 0.8)):
            if dl.get(k):
                out.append(("high", dl[k], k.upper(), imp))
        for k, imp in (("pwl", 0.9), ("pdl", 0.8)):
            if dl.get(k):
                out.append(("low", dl[k], k.upper(), imp))
        if ctx.asia and ctx.asia.get("complete"):
            out.append(("high", ctx.asia["high"], "Asia high", 0.7))
            out.append(("low", ctx.asia["low"], "Asia low", 0.7))
        for t in REF_TFS[tf]:
            dd = ctx.tf.get(t)
            if not dd:
                continue
            for lq in dd.liquidity:
                out.append(("high" if lq.kind == "eqh" else "low", lq.mid, f"{t} {lq.kind.upper()}", 0.75))
            st = dd.structure or {}
            if st.get("last_high") is not None:
                out.append(("high", st["last_high"].price, f"{t} swing high", 0.5))
            if st.get("last_low") is not None:
                out.append(("low", st["last_low"].price, f"{t} swing low", 0.5))
        return out

    def liquidity_goals(self, ctx, tf, direction, entry, risk, atr, min_rr, tg) -> list[tuple[float, str]]:
        """Opposing liquidity pools in trade direction, nearest first, as (target price, label) within RR bounds."""
        sign = 1 if direction == "long" else -1
        want = "high" if direction == "long" else "low"
        front = float(tg.get("liq_front_atr", 0.1)) * (atr or 0.0)
        max_rr = float(tg.get("liq_max_rr", 6.0))
        seen: dict[float, str] = {}
        for side, lvl, label, _imp in self._liquidity_targets(ctx, tf):
            if side == want and lvl is not None and (lvl - entry) * sign > 0:
                seen.setdefault(round(float(lvl), 6), label)
        out = []
        for lvl, label in sorted(seen.items(), key=lambda kv: (kv[0] - entry) * sign):
            g = lvl - sign * front
            rr = (g - entry) * sign / risk if risk > 0 else 0.0
            if min_rr - 1e-6 <= rr <= max_rr:
                out.append((g, label))
        return out

    def _s_sweep_reversal(self, ctx, tf, d, profile):
        out = []
        b = d.bars
        for side, lvl, label, imp in self._liquidity_targets(ctx, tf):
            sw = liquidity.detect_sweep(b, lvl, side, 3)
            if not sw:
                continue
            direction = sw["direction"]
            conf = self.confirmations(ctx, tf, direction)
            if not conf:
                continue
            ext = float(b["high"].iloc[-4:].max()) if direction == "short" else float(b["low"].iloc[-4:].min())
            out.append({"direction": direction, "anchor": ext, "zone": (min(lvl, ext), max(lvl, ext)),
                        "zone_id": f"sweep:{label}:{lvl:.6g}", "zone_quality": imp,
                        "notes": [f"swept {label} {lvl:.6g} and reclaimed"], "edge": lvl})
        return out

    def _s_bos_retest(self, ctx, tf, d, profile):
        st = d.structure
        out = []
        b = d.bars
        n = d.n
        atr = d.atr
        for ev in reversed(st.get("events", [])):
            if n - 1 - ev.idx > 25:
                break
            if n - 1 - ev.idx < 2:
                continue
            p = ev.price
            last = b.iloc[-1]
            seg = b.iloc[-3:]
            if ev.direction == "bull":
                if seg["low"].min() <= p + 0.3 * atr and last.close > p and last.close > last.open:
                    out.append({"direction": "long", "anchor": min(float(seg["low"].min()), p - 0.1 * atr),
                                "zone": (p - 0.3 * atr, p + 0.3 * atr), "zone_id": f"bos:{tf}:{ev.time}",
                                "zone_quality": 0.6 + (0.15 if ev.kind == "BOS" else 0.05),
                                "notes": [f"retest of {tf} {ev.kind} {p:.6g}"], "edge": p})
            else:
                if seg["high"].max() >= p - 0.3 * atr and last.close < p and last.close < last.open:
                    out.append({"direction": "short", "anchor": max(float(seg["high"].max()), p + 0.1 * atr),
                                "zone": (p - 0.3 * atr, p + 0.3 * atr), "zone_id": f"bos:{tf}:{ev.time}",
                                "zone_quality": 0.6 + (0.15 if ev.kind == "BOS" else 0.05),
                                "notes": [f"retest of {tf} {ev.kind} {p:.6g}"], "edge": p})
            break
        return out

    def _s_fvg_ob(self, ctx, tf, d, profile):
        out = []
        b = d.bars
        last = b.iloc[-1]
        bias = ctx.htf_bias.get("bias", "neutral")
        for t in REF_TFS[tf]:
            dd = ctx.tf.get(t)
            if not dd:
                continue
            for z in dd.fvgs + dd.obs:
                bull = z.kind in ("fvg_bull", "ob_bull")
                if bull and bias == "bear" or (not bull and bias == "bull"):
                    continue
                if bull and float(b["low"].iloc[-2:].min()) <= z.high and last.close > z.low and last.close > last.open:
                    if self.confirmations(ctx, tf, "long"):
                        out.append({"direction": "long", "anchor": min(z.low, float(b["low"].iloc[-3:].min())),
                                    "zone": (z.low, z.high), "zone_id": f"{z.kind}:{t}:{z.created}",
                                    "zone_quality": z.strength + (0.1 if t != tf else 0),
                                    "notes": [f"{t} {z.kind}"], "edge": z.high})
                if not bull and float(b["high"].iloc[-2:].max()) >= z.low and last.close < z.high and last.close < last.open:
                    if self.confirmations(ctx, tf, "short"):
                        out.append({"direction": "short", "anchor": max(z.high, float(b["high"].iloc[-3:].max())),
                                    "zone": (z.low, z.high), "zone_id": f"{z.kind}:{t}:{z.created}",
                                    "zone_quality": z.strength + (0.1 if t != tf else 0),
                                    "notes": [f"{t} {z.kind}"], "edge": z.low})
        return out

    def _s_range_breakout(self, ctx, tf, d, profile):
        b = d.bars
        if d.n < 20:
            return []
        seg = b.iloc[-13:-1]
        hi, lo = float(seg["high"].max()), float(seg["low"].min())
        atr_prev = float(d.atr_series[-2]) if d.atr_series is not None else d.atr
        if hi - lo > 2.2 * atr_prev:
            return []
        last = b.iloc[-1]
        rng = float(last.high - last.low)
        rv_ok = True
        if ctx.has_volume and d.rvol is not None:
            rv_ok = float(np.nan_to_num(d.rvol[-1], nan=0)) >= 1.5
        if rng < 1.3 * atr_prev or not rv_ok:
            return []
        mid = (hi + lo) / 2
        q = 0.55 + (0.15 if ctx.atr_regime in ("compression", "expansion") else 0)
        if last.close > hi:
            return [{"direction": "long", "anchor": mid, "zone": (lo, hi), "zone_id": f"rb:{tf}:{int(seg.time.iloc[0])}",
                     "zone_quality": q, "notes": [f"range {lo:.6g}-{hi:.6g} breakout, bar {rng / atr_prev:.1f}xATR"],
                     "edge": hi}]
        if last.close < lo:
            return [{"direction": "short", "anchor": mid, "zone": (lo, hi), "zone_id": f"rb:{tf}:{int(seg.time.iloc[0])}",
                     "zone_quality": q, "notes": [f"range {lo:.6g}-{hi:.6g} breakdown, bar {rng / atr_prev:.1f}xATR"],
                     "edge": lo}]
        return []

    def _s_orb(self, ctx, tf, d, profile):
        out = []
        b = d.bars
        mins = int(profile.get("orb_minutes", 15))
        m1 = ctx.bars("M1")
        if m1 is None or len(m1) < mins:
            return out
        for name in ("orb_london", "orb_ny"):
            w = dict(self.sessions[name])
            w["end"] = w.get("end", w["start"])
            start, _ = window_bounds(ctx.now, {"tz": w["tz"], "start": w["start"], "end": w["start"]})
            or_end = start + mins * 60
            if not (or_end <= ctx.now <= start + 120 * 60):
                continue
            seg = m1[(m1["time"] >= start) & (m1["time"] < or_end)]
            if len(seg) < mins * 0.6:
                continue
            hi, lo = float(seg["high"].max()), float(seg["low"].min())
            last, prev = b.iloc[-1], b.iloc[-2]
            rv_ok = True
            if ctx.has_volume and d.rvol is not None:
                rv_ok = float(np.nan_to_num(d.rvol[-1], nan=0)) >= 1.2
            mid = (hi + lo) / 2
            for direction, cond in (("long", last.close > hi and prev.close <= hi),
                                    ("short", last.close < lo and prev.close >= lo)):
                key = (ctx.symbol, name, start, direction)
                if not cond or not rv_ok or key in self._orb_used:
                    continue
                self._orb_used.add(key)
                anchor = mid if profile.get("orb_stop", "mid") == "mid" else (lo if direction == "long" else hi)
                out.append({"direction": direction, "anchor": anchor, "zone": (lo, hi),
                            "zone_id": f"orb:{name}:{start}", "zone_quality": 0.65,
                            "notes": [f"{name.replace('orb_', '').upper()} ORB {mins}m {lo:.6g}-{hi:.6g}"],
                            "edge": hi if direction == "long" else lo})
        return out

    def _s_vwap_reversion(self, ctx, tf, d, profile):
        dv = ctx.vwap.get("daily")
        if not dv or dv["last_sd"] <= 0:
            return []
        b = d.bars
        last = b.iloc[-1]
        u2, l2 = float(dv["u2"][-1]), float(dv["l2"][-1])
        hi3, lo3 = float(b["high"].iloc[-3:].max()), float(b["low"].iloc[-3:].min())
        f = d.flags or {}
        flow = []
        if f.get("absorption") is not None and f["absorption"][-3:].any():
            flow.append("absorption")
        out = []
        rej_bear = candles.rejection(b) == "bear" or candles.engulfing(b) == "bear"
        rej_bull = candles.rejection(b) == "bull" or candles.engulfing(b) == "bull"
        if hi3 > u2 and last.close < u2 and rej_bear:
            if flow or not ctx.has_volume or "delta" in self.confirmations(ctx, tf, "short"):
                out.append({"direction": "short", "anchor": hi3, "zone": (u2, hi3), "zone_id": f"vwap:u2:{ctx.daily.get('day_start')}",
                            "zone_quality": 0.6, "notes": ["fade +2σ daily VWAP"] + flow, "edge": u2,
                            "target_hint": dv["last"]})
        if lo3 < l2 and last.close > l2 and rej_bull:
            if flow or not ctx.has_volume or "delta" in self.confirmations(ctx, tf, "long"):
                out.append({"direction": "long", "anchor": lo3, "zone": (lo3, l2), "zone_id": f"vwap:l2:{ctx.daily.get('day_start')}",
                            "zone_quality": 0.6, "notes": ["fade -2σ daily VWAP"] + flow, "edge": l2,
                            "target_hint": dv["last"]})
        return out

    def _s_wyckoff(self, ctx, tf, d, profile):
        reg = ctx.regime.get("regime")
        st = d.structure
        if reg not in ("range", "compression") and st.get("trend") != "neutral":
            return []
        dr = st.get("dealing_range")
        if not dr:
            return []
        b = d.bars
        last = b.iloc[-1]
        rv = float(np.nan_to_num(d.rvol[-1], nan=0)) if d.rvol is not None else None
        f = d.flags or {}
        out = []
        if last.low < dr["low"] and last.close > dr["low"]:
            vol_note = "spring on volume" if (rv and rv >= 1.3) else (
                "spring no-supply test" if f.get("no_supply") is not None and f["no_supply"][-3:].any() else "spring")
            out.append({"direction": "long", "anchor": float(last.low), "zone": (float(last.low), dr["low"]),
                        "zone_id": f"wyckoff:{tf}:{dr['low']:.6g}", "zone_quality": 0.6,
                        "notes": [f"{vol_note} below range low {dr['low']:.6g}"], "edge": dr["low"], "setup": "wyckoff"})
        if last.high > dr["high"] and last.close < dr["high"]:
            vol_note = "upthrust on volume" if (rv and rv >= 1.3) else "upthrust"
            out.append({"direction": "short", "anchor": float(last.high), "zone": (dr["high"], float(last.high)),
                        "zone_id": f"wyckoff:{tf}:{dr['high']:.6g}", "zone_quality": 0.6,
                        "notes": [f"{vol_note} above range high {dr['high']:.6g}"], "edge": dr["high"], "setup": "wyckoff"})
        return out

    def _s_london_judas(self, ctx, tf, d, profile):
        a = ctx.asia
        if not a or not a.get("complete"):
            return []
        b = d.bars
        start, _ = window_bounds(ctx.now, self.sessions["london_open"])
        seg = b[b["time"] >= start]
        if len(seg) < 2:
            return []
        out = []
        if seg["high"].max() > a["high"] and b.iloc[-1].close < a["high"]:
            if any(c.startswith("CHoCH") or c in ("engulfing", "rejection pin") for c in self.confirmations(ctx, tf, "short")):
                out.append({"direction": "short", "anchor": float(seg["high"].max()), "zone": (a["high"], float(seg["high"].max())),
                            "zone_id": f"judas:{int(a['start'])}:short", "zone_quality": 0.75,
                            "notes": [f"London Judas: swept Asia high {a['high']:.6g}"], "edge": a["high"]})
        if seg["low"].min() < a["low"] and b.iloc[-1].close > a["low"]:
            if any(c.startswith("CHoCH") or c in ("engulfing", "rejection pin") for c in self.confirmations(ctx, tf, "long")):
                out.append({"direction": "long", "anchor": float(seg["low"].min()), "zone": (float(seg["low"].min()), a["low"]),
                            "zone_id": f"judas:{int(a['start'])}:long", "zone_quality": 0.75,
                            "notes": [f"London Judas: swept Asia low {a['low']:.6g}"], "edge": a["low"]})
        return out

    def _s_ny_open_reversal(self, ctx, tf, d, profile):
        b = d.bars
        ny_start, _ = window_bounds(ctx.now, self.sessions["ny_open"])
        ld_start, _ = window_bounds(ny_start - 60, self.sessions["london"])
        lon = b[(b["time"] >= ld_start) & (b["time"] < ny_start)]
        ny = b[b["time"] >= ny_start]
        if len(lon) < 5 or len(ny) < 2:
            return []
        lh, ll = float(lon["high"].max()), float(lon["low"].min())
        out = []
        if ny["high"].max() > lh and b.iloc[-1].close < lh and self.confirmations(ctx, tf, "short"):
            out.append({"direction": "short", "anchor": float(ny["high"].max()), "zone": (lh, float(ny["high"].max())),
                        "zone_id": f"nyrev:{ny_start}:short", "zone_quality": 0.7,
                        "notes": [f"NY open swept London high {lh:.6g}"], "edge": lh})
        if ny["low"].min() < ll and b.iloc[-1].close > ll and self.confirmations(ctx, tf, "long"):
            out.append({"direction": "long", "anchor": float(ny["low"].min()), "zone": (float(ny["low"].min()), ll),
                        "zone_id": f"nyrev:{ny_start}:long", "zone_quality": 0.7,
                        "notes": [f"NY open swept London low {ll:.6g}"], "edge": ll})
        return out

    def _s_second_leg(self, ctx, tf, d, profile):
        m15 = ctx.tf.get("M15")
        if m15 is None or m15.n < 30:
            return []
        ev = structure.recent_event(m15.structure, 30, m15.n, kind="BOS")
        if ev is None:
            return []
        b = d.bars
        last = b.iloc[-1]
        out = []
        for f in m15.fvgs:
            if ev.direction == "bull" and f.kind == "fvg_bull" and f.created >= int(m15.bars["time"].iloc[ev.swing_idx]):
                if float(b["low"].iloc[-3:].min()) <= f.high and last.close > f.low and self.confirmations(ctx, tf, "long"):
                    out.append({"direction": "long", "anchor": min(f.low, float(b["low"].iloc[-3:].min())),
                                "zone": (f.low, f.high), "zone_id": f"2leg:{f.created}", "zone_quality": 0.65,
                                "notes": [f"second leg after M15 BOS {ev.price:.6g}, pullback into M15 FVG"], "edge": f.high})
            if ev.direction == "bear" and f.kind == "fvg_bear" and f.created >= int(m15.bars["time"].iloc[ev.swing_idx]):
                if float(b["high"].iloc[-3:].max()) >= f.low and last.close < f.high and self.confirmations(ctx, tf, "short"):
                    out.append({"direction": "short", "anchor": max(f.high, float(b["high"].iloc[-3:].max())),
                                "zone": (f.low, f.high), "zone_id": f"2leg:{f.created}", "zone_quality": 0.65,
                                "notes": [f"second leg after M15 BOS {ev.price:.6g}, pullback into M15 FVG"], "edge": f.low})
        return out

    def _s_asia_range_reversion(self, ctx, tf, d, profile):
        a = ctx.asia
        if not a or a.get("complete"):
            return []
        if ctx.now - a["start"] < 2 * 3600:
            return []
        b = d.bars
        atr = d.atr
        out = []
        mid = (a["high"] + a["low"]) / 2
        if a["high"] - a["low"] < 2 * atr:
            return []
        if float(b["high"].iloc[-2:].max()) >= a["high"] - 0.2 * atr and candles.rejection(b) == "bear":
            out.append({"direction": "short", "anchor": float(b["high"].iloc[-3:].max()), "zone": (a["high"] - 0.2 * atr, a["high"]),
                        "zone_id": f"asia:{int(a['start'])}:h", "zone_quality": 0.55, "notes": ["Asia range high fade"],
                        "edge": a["high"], "target_hint": mid})
        if float(b["low"].iloc[-2:].min()) <= a["low"] + 0.2 * atr and candles.rejection(b) == "bull":
            out.append({"direction": "long", "anchor": float(b["low"].iloc[-3:].min()), "zone": (a["low"], a["low"] + 0.2 * atr),
                        "zone_id": f"asia:{int(a['start'])}:l", "zone_quality": 0.55, "notes": ["Asia range low fade"],
                        "edge": a["low"], "target_hint": mid})
        return out

    # ------------------------------------------------------------------ finalize: stops, targets, RR, score
    def _skip(self, ctx, raw, reason: str) -> None:
        key = (ctx.symbol, raw.get("setup"), raw.get("tf"), raw.get("direction"), reason.split(" ")[0],
               int(ctx.tf[raw["tf"]].last_bar_time))
        rec = {"ts": ctx.now, "symbol": ctx.symbol, "setup": raw.get("setup"), "tf": raw.get("tf"),
               "direction": raw.get("direction"), "reason": reason}
        self.skips.setdefault(ctx.symbol, []).append(rec)
        self.skips[ctx.symbol] = self.skips[ctx.symbol][-30:]
        if key not in self._skip_seen:
            self._skip_seen.add(key)
            if len(self._skip_seen) > 5000:
                self._skip_seen.clear()
            self.log("SKIP", f"SKIPPED {ctx.symbol} {raw.get('setup')} {raw.get('tf')} {raw.get('direction')}: {reason}",
                     ctx.symbol)

    def finalize(self, ctx: SymbolContext, raw: dict, profile: dict, dxy_state: dict | None) -> Candidate | None:
        tf = raw["tf"]
        d = ctx.tf[tf]
        atr = d.atr
        direction = raw["direction"]
        sign = 1 if direction == "long" else -1
        tick = ctx.tick
        if tick is None:
            self._skip(ctx, raw, "no live quote")
            return None
        min_rr = self.cfg.min_rr
        # --- ADR exhaustion
        if ctx.adr_used is not None and ctx.adr_used >= float(self.sc["adr_exhaustion_pct"]):
            day_dir = 1 if (ctx.price or 0) >= (ctx.daily.get("daily_open") or 0) else -1
            if day_dir == sign:
                self._skip(ctx, raw, f"ADR {ctx.adr_used:.0f}% used - no new entries in the move's direction")
                return None
        # --- entry
        market_entry = tick.ask if direction == "long" else tick.bid
        entry_type = raw.get("entry_type", "market")
        edge = raw.get("edge")
        entry = market_entry
        if entry_type == "limit" and edge is not None:
            if (direction == "long" and market_entry - edge > 0.3 * atr) or (direction == "short" and edge - market_entry > 0.3 * atr):
                entry = edge + (tick.spread if direction == "long" else 0.0)
            else:
                entry_type = "market"
        else:
            entry_type = "market"
        # --- stop
        st = profile["stops"]
        style = st.get("style", "atr")
        spread_floor = float(st.get("min_spread_mult", 5.0)) * max(tick.spread, 0.0)
        ref_atr = atr
        if style == "atr":
            # ATR stop on a slower timeframe so M1/M5 entries are not stopped by noise or spread
            ref_tf = st.get("atr_tf", "M15")
            ref_atr = ctx.atr(ref_tf) or atr
            dist = max(float(st.get("atr_mult", 1.5)) * ref_atr, spread_floor)
            stop = entry - sign * dist
            risk = dist
        else:
            # structure anchor + ATR buffer
            stop = raw["anchor"] - sign * float(st["atr_buffer_mult"]) * atr
            risk = (entry - stop) * sign
            if risk <= 0:
                self._skip(ctx, raw, "invalid stop (entry beyond anchor)")
                return None
            floor = max(float(st["min_atr"]) * atr, spread_floor)
            if risk < floor:
                stop = entry - sign * floor
                risk = floor
            if risk > float(st["max_atr"]) * atr:
                self._skip(ctx, raw, f"stop too wide ({risk / atr:.1f}xATR > {st['max_atr']})")
                return None
        # --- obstacle & targets (never stretch TP beyond the real obstacle)
        tg = profile["targets"]
        tp1_rr = max(float(tg["tp1_rr"]), min_rr)
        obstacles = self.levels.opposing(ctx.symbol, direction, entry)
        obstacle = None
        for ob in obstacles:
            near = ob["low"] if direction == "long" else ob["high"]
            if (near - entry) * sign > 0.15 * risk:
                obstacle = near
                break
        hint = raw.get("target_hint")
        if hint is not None and (hint - entry) * sign > 0 and (obstacle is None or (hint - obstacle) * sign < 0):
            obstacle = hint
        rr_obstacle = None
        if obstacle is not None:
            rr_obstacle = (obstacle - entry) * sign / risk
            if rr_obstacle < max(min_rr, tp1_rr) - 1e-6:
                self._skip(ctx, raw, f"RR {rr_obstacle:.2f} < {max(min_rr, tp1_rr):.1f} (next obstacle {obstacle:.6g})")
                return None
            if rr_obstacle > 4.0:
                obstacle = None
                rr_obstacle = None
        tp1 = entry + sign * tp1_rr * risk
        if obstacle is not None:
            tp2 = obstacle - sign * 0.1 * atr
            if (tp2 - entry) * sign / risk < tp1_rr + 0.2:
                tp2 = tp1
        else:
            tp2 = entry + sign * max(float(tg["tp2_rr_default"]), tp1_rr) * risk
        if tg.get("mode") == "liquidity":
            # liquidity -> liquidity: aim at the next opposing liquidity pool (PDH/PDL, PWH/PWL, Asia high/low,
            # equal highs/lows, HTF swings), slightly in front of it; TP2 = the following pool.
            goals = self.liquidity_goals(ctx, tf, direction, entry, risk, atr, min_rr, tg)
            if not goals:
                self._skip(ctx, raw, f"no opposing liquidity between {min_rr:.1f}R and {tg.get('liq_max_rr', 6.0)}R")
                return None
            tp1, lab1 = goals[0]
            tp2 = tp1
            for g, _lab in goals[1:]:
                if (g - tp1) * sign / risk >= 0.5:
                    tp2 = g
                    break
            raw.setdefault("notes", []).append(f"target: {lab1} liquidity")
        rr_tp1 = (tp1 - entry) * sign / risk
        rr_final = (tp2 - entry) * sign / risk
        if rr_tp1 < min_rr - 1e-6 or rr_final < min_rr - 1e-6:
            self._skip(ctx, raw, f"RR {min(rr_tp1, rr_final):.2f} < {min_rr:.1f}")
            return None
        # --- score components
        w = self.sc["weights"]
        conf = self.confirmations(ctx, tf, direction)
        lv = self.levels.level_for(ctx.symbol, *raw["zone"]) if raw.get("zone") else None
        comps = {}
        comps["zone_quality"] = max(0.0, min(1.0, raw.get("zone_quality", 0.5)))
        if lv and lv["grade"] in ("A+", "A", "B"):
            comps["confluence"] = min(1.0, (lv["n_sources"] - 1) / 6.0) * 0.6 + 0.4 * (lv["score"] / 100.0)
        else:
            comps["confluence"] = 0.0
        comps["confirmation"] = min(1.0, len(conf) / 3.0)
        hb = ctx.htf_bias or {}
        if hb.get("votes"):
            agree = sum(v * sign for v in hb["votes"].values())
            align = (agree + hb["n"]) / (2.0 * hb["n"])
        else:
            align = 0.5
        if raw["setup"] in REVERSAL_SETUPS and any(c.startswith("CHoCH") for c in conf):
            align = max(align, 0.5)
        comps["bias"] = max(0.0, min(1.0, align))
        vf, vnotes = self.volume_flow(ctx, tf, direction)
        comps["volume_flow"] = vf
        if edge is not None:
            comps["proximity"] = max(0.0, 1.0 - abs(entry - edge) / atr)
        else:
            comps["proximity"] = 0.5
        r_atr = risk / ref_atr
        # stop quality depends on its size in ATR of the stop timeframe (0.8..2.0 ideal)
        if 0.8 <= r_atr <= 2.0:
            sq = 1.0
        elif r_atr < 0.8:
            sq = max(0.0, r_atr / 0.8)
        else:
            sq = max(0.0, 1.0 - (r_atr - 2.0) / 2.0)
        comps["stop_quality"] = 0.4 + 0.6 * sq
        cond = 0.0
        if tick.spread <= 0.10 * atr:
            cond += 0.4
        if self._session_ok(profile["playbooks"][raw["setup"]].get("sessions", []), ctx.now):
            cond += 0.3
        if ctx.adr_used is not None and ctx.adr_used < 60:
            cond += 0.3
        comps["conditions"] = cond
        base = sum(float(w[k]) * comps[k] for k in w)
        # --- modifiers
        caps = self.sc["modifier_caps"]
        mods = {}
        if lv:
            if lv.get("state") == "READY":
                mods["levels_ready"] = min(caps["levels_ready"], GRADE_READY_BONUS.get(lv["grade"], 2))
            elif lv.get("state") == "STALLED":
                mods["levels_ready"] = -caps["levels_ready"]
                if raw["setup"] == "zone_reaction":
                    self._skip(ctx, raw, f"level {lv['mid']:.6g} STALLED (no reaction in time)")
                    return None
        dm = dxy_modifier(ctx.symbol, direction, dxy_state if (dxy_state or {}).get("available") else None,
                          ((dxy_state or {}).get("correlations") or {}).get(ctx.symbol), self.cfg.get("dxy"))
        mods["dxy"] = dm["points"]
        reg = ctx.regime.get("regime")
        rp = (profile.get("regime_preferences") or {}).get(reg or "", {})
        if raw["setup"] in rp.get("prefer", []):
            mods["regime"] = round(caps["regime"] * max(0.5, ctx.regime.get("confidence", 0.5)), 2)
        elif raw["setup"] in rp.get("avoid", []):
            mods["regime"] = -caps["regime"]
        if self.intel is not None:
            sm = self.intel.score_modifier(ctx.symbol, direction)
            mods["sentiment_macro"] = max(-caps["sentiment_macro"], min(caps["sentiment_macro"], sm["points"]))
        if self.analyst is not None:
            dt = to_dt(ctx.now)
            mods["seasonality"] = max(-caps["seasonality"], min(caps["seasonality"],
                                      self.analyst.seasonality_modifier(ctx.symbol, raw["setup"], dt.hour, dt.weekday())))
            mods["analyst"] = max(-caps["analyst"], min(caps["analyst"], self.analyst.setup_adjust(raw["setup"])))
        pw = float(profile["playbooks"][raw["setup"]].get("weight", 1.0))
        mods["playbook_weight"] = round(base * (pw - 1.0) * 0.5, 2)
        total_mod = sum(v for v in mods.values())
        total_mod = max(-caps["total"], min(caps["total"], total_mod))
        score = max(0.0, min(100.0, base + total_mod))
        c = Candidate(
            symbol=ctx.symbol, direction=direction, setup=raw["setup"], timeframe=tf, entry=float(entry),
            entry_type=entry_type, stop=float(stop), tp1=float(tp1), tp2=float(tp2), obstacle=obstacle,
            score=round(score, 1), base_score=round(base, 1), components={k: round(v, 3) for k, v in comps.items()},
            modifiers={k: round(v, 2) for k, v in mods.items()}, confirmations=conf,
            reasons=raw.get("notes", []) + vnotes + dm["notes"], zone_id=raw.get("zone_id", ""),
            session=ctx.session, regime=reg or "", created=ctx.now, rr_tp1=round(rr_tp1, 2),
            rr_final=round(rr_final, 2), rr_obstacle=round(rr_obstacle, 2) if rr_obstacle else None,
        )
        c.id = f"{ctx.symbol}-{raw['setup']}-{tf}-{direction}-{int(d.last_bar_time)}"
        if dm["block"]:
            c.modifiers["dxy_block"] = 1
            c.reasons.append("DXY block")
        if self.analyst is not None and self.analyst.setup_disabled(raw["setup"]):
            c.modifiers["setup_disabled"] = 1
        return c

    # ------------------------------------------------------------------ main
    def evaluate(self, ctx: SymbolContext, dxy_state: dict | None, active_zone_ids: set | None = None) -> list[Candidate]:
        profile = self.strategist.profile(ctx.symbol)
        if ctx.preview and not self.cfg.get("system.paper_on_fallback", True):
            return []
        sess_ok = self._session_ok(profile.get("sessions_allowed", []), ctx.now)
        if ctx.symbol in ("XAUUSD", "XAGUSD") and ctx.session == "asia" and not profile.get("asia_opt_in"):
            sess_ok = False
        out: list[Candidate] = []
        tfs = [tf for tf in profile.get("entry_timeframes", []) if tf in self.sc["entry_timeframes"]]
        for tf in tfs:
            d = ctx.tf.get(tf)
            if d is None or d.n < 30:
                continue
            key = (ctx.symbol, tf)
            if self.last_eval.get(key) == d.last_bar_time:
                continue
            self.last_eval[key] = d.last_bar_time
            if not sess_ok:
                continue
            for raw in self.setups(ctx, tf, profile):
                c = self.finalize(ctx, raw, profile, dxy_state)
                if c is None:
                    continue
                out.append(c)
        if not out:
            return []
        best: dict[tuple, Candidate] = {}
        for c in out:
            k = (c.direction, c.zone_id)
            if active_zone_ids and c.zone_id in active_zone_ids:
                continue
            if k not in best or c.score > best[k].score:
                best[k] = c
        res = sorted(best.values(), key=lambda c: -c.score)
        self.latest[ctx.symbol] = res
        for c in res:
            self.log("SIGNAL", f"{c.symbol} {c.direction.upper()} {c.setup} {c.timeframe} score {c.score:.0f} "
                     f"(base {c.base_score:.0f}) RR {c.rr_final:.2f} · {', '.join(c.confirmations) or 'no LTF conf'}",
                     c.symbol, {"id": c.id})
        self.set_status(f"{ctx.symbol}: {len(res)} candidate(s), best {res[0].score:.0f}")
        self.pulse(res[0].score)
        return res
