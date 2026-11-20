"""Chart payload for the trading floor: candles + every toggleable layer, from the live Scanner/Levels state.
All values come from the current analysis feed; nothing is invented. Missing data -> empty layer + note."""
from __future__ import annotations

import numpy as np

from aurum_nexus.indicators import vwap as vwm
from aurum_nexus.models import TF_SECONDS
from aurum_nexus.timeutil import window_bounds

TF_FOR_ZONES = {"M1": ["M5", "M15", "H1"], "M5": ["M5", "M15", "H1", "H4"], "M15": ["M15", "H1", "H4"],
                "H1": ["H1", "H4", "D1"], "H4": ["H4", "D1"], "D1": ["D1"]}


def _lv(level) -> dict:
    return {"kind": level.kind, "low": level.low, "high": level.high, "tf": level.timeframe, "from": level.created,
            "fresh": level.fresh, "touches": level.touches, "strength": round(level.strength, 2), "meta": level.meta}


def build_chart(engine, symbol: str, tf: str, limit: int = 400) -> dict:
    feed = engine.feed
    ctx = engine.scanner.contexts.get(symbol)
    q = feed.quote_status(symbol)
    bars = feed.bars(symbol, tf, limit, closed=False)
    out: dict = {"symbol": symbol, "tf": tf, "quote": q, "candles": [], "layers": {}, "notes": []}
    if bars is None or len(bars) == 0:
        out["notes"].append("NO DATA")
        return out
    t = bars["time"].to_numpy("int64")
    out["candles"] = [{"time": int(r.time), "open": float(r.open), "high": float(r.high), "low": float(r.low),
                       "close": float(r.close)} for r in bars.itertuples(index=False)]
    vol = bars["real_volume"].to_numpy(float)
    if vol.sum() <= 0:
        vol = bars["tick_volume"].to_numpy(float)
    d = ctx.tf.get(tf) if ctx else None
    rv_map, cvd_map = {}, {}
    if d is not None and d.n:
        dt = d.bars["time"].to_numpy("int64")
        if d.rvol is not None:
            rv_map = {int(a): (None if not np.isfinite(b) else float(b)) for a, b in zip(dt, d.rvol)}
        if d.cvd is not None:
            cvd_map = {int(a): float(b) for a, b in zip(dt, d.cvd)}
    has_vol = bool(ctx.has_volume) if ctx else False
    out["has_volume"] = has_vol
    out["layers"]["volume"] = [{"time": int(a), "value": float(v), "rvol": rv_map.get(int(a))} for a, v in zip(t, vol)] \
        if has_vol else []
    out["layers"]["cvd"] = [{"time": k, "value": v} for k, v in cvd_map.items() if k >= t[0]] if has_vol else []
    if not has_vol:
        out["notes"].append("volume tools disabled: this source has no volume")
    if ctx is None:
        out["notes"].append("analysis not ready yet")
        return out
    # structure (BOS / CHoCH) and divergences on this timeframe
    if d is not None and d.structure:
        out["layers"]["structure"] = [{"time": e.time, "price": e.price, "kind": e.kind, "dir": e.direction,
                                       "from": int(d.bars["time"].iloc[e.swing_idx])}
                                      for e in d.structure.get("events", [])[-30:] if e.time >= t[0]]
        out["layers"]["swings"] = [{"time": s.time, "price": s.price, "label": s.label}
                                   for s in d.structure.get("swings", [])[-40:] if s.time >= t[0]]
        out["layers"]["divergences"] = [{"time": x["time"], "type": x["type"], "price": x["price"]}
                                        for x in d.divergences[-10:] if x["time"] >= t[0]]
    zones, fvg, liq = [], [], []
    for z_tf in TF_FOR_ZONES.get(tf, [tf]):
        dd = ctx.tf.get(z_tf)
        if not dd:
            continue
        zones += [_lv(z) for z in dd.zones]
        fvg += [_lv(z) for z in dd.fvgs + dd.obs]
        liq += [_lv(z) for z in dd.liquidity]
    out["layers"]["zones"] = zones
    out["layers"]["fvg_ob"] = fvg
    out["layers"]["liquidity"] = liq
    dl = ctx.daily or {}
    out["layers"]["key_levels"] = [{"label": k.upper(), "price": dl[k]} for k in ("pdh", "pdl", "pwh", "pwl")
                                   if dl.get(k)] + ([{"label": "D-OPEN", "price": dl["daily_open"]}]
                                                    if dl.get("daily_open") else [])
    if ctx.asia:
        out["layers"]["asia"] = {"high": ctx.asia["high"], "low": ctx.asia["low"], "start": ctx.asia["start"],
                                 "end": ctx.asia["end"]}
    # session boxes over the visible range
    sess = []
    sessions = engine.cfg.get("sessions")
    day = 86400
    t_end = int(t[-1]) + TF_SECONDS.get(tf, 60)
    probe = int(t[0])
    seen = set()
    while probe <= t_end + day:
        for name in ("asia", "london", "newyork"):
            s, e = window_bounds(probe, sessions[name])
            if (name, s) in seen or e < t[0] or s > t_end:
                continue
            seen.add((name, s))
            seg = bars[(bars["time"] >= s) & (bars["time"] < e)]
            if len(seg):
                sess.append({"name": name, "start": s, "end": e, "high": float(seg["high"].max()),
                             "low": float(seg["low"].min())})
        probe += 6 * 3600
    out["layers"]["sessions"] = sess
    # volume profile (composite + daily + session)
    vp = {}
    for k in ("composite", "daily", "session"):
        p = ctx.vp.get(k)
        if p:
            vp[k] = {"poc": p["poc"], "vah": p["vah"], "val": p["val"], "bins": p["bins"], "volumes": p["volumes"],
                     "basis": p["basis"], "shape": p["shape"], "hvn": p["hvn"], "lvn": p["lvn"]}
    out["layers"]["volume_profile"] = vp
    # VWAPs (+ bands)
    out["layers"]["vwap"] = {k: vwm.to_series(ctx.vwap.get(k), 2000) for k in ("daily", "weekly", "anchored")
                             if ctx.vwap.get(k)}
    for k, s in out["layers"]["vwap"].items():
        for band in ("vwap", "u1", "l1", "u2", "l2"):
            s[band] = [p for p in s.get(band, []) if p["time"] >= t[0]]
    # daily levels from the Levels agent
    out["layers"]["daily_levels"] = [{k: lv.get(k) for k in ("id", "low", "high", "mid", "grade", "state", "role",
                                                               "expected", "score", "sources", "countdown")}
                                     for lv in engine.levels.maps.get(symbol, [])]
    out["context"] = {"session": ctx.session, "regime": ctx.regime, "adr": ctx.adr, "adr_used": ctx.adr_used,
                      "atr": {k: v.atr for k, v in ctx.tf.items()}, "htf_bias": ctx.htf_bias}
    return out
