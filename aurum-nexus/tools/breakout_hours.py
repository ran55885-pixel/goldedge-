"""Breakout quality by hour of day (M5), for one symbol's M1 history CSV (docs/BACKTEST_CSV.md).

Question it answers: at which hours do M5 breakouts run to 1.4R-2.0R, and at which hours are they fakes?

Method (strategy-agnostic, no prediction, no moving averages):
  * Breakout (default --mode structure): an M5 structure break of the system's own swing structure
    (indicators/structure.py): a close beyond the last confirmed swing high/low = BOS (with trend) or CHoCH (against).
    --mode range: a close beyond the highest high / lowest low of the previous --lookback M5 bars.
  * Entry at that close, paying the bar's spread (long buys at ask). 1R = max(--atr-mult x ATR14 on M15,
    --min-spread-mult x spread) - the same ATR stop model as the live system.
  * Path walked on M1 bars for --horizon minutes, bid/ask aware. If stop and a level are touched inside the same
    M1 bar, the stop is counted first (conservative). MFE = best excursion in R before the stop.
  * strong = reached >= 1.4R before the stop; very strong = >= 2.0R.
    fake = stopped out after less than 0.5R; back-inside = an M5 close back through the broken swing/level within 15 min.
Hours are shown in Israel time (Asia/Jerusalem) and UTC.

usage: python tools/breakout_hours.py XAUUSD.csv [--out DIR] [--point 0.01]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aurum_nexus.backtest.csvio import read_m1_csv  # noqa: E402
from aurum_nexus.indicators.atr import atr  # noqa: E402
from aurum_nexus.indicators.structure import analyze  # noqa: E402

TZ = "Asia/Jerusalem"


def resample(m1: pd.DataFrame, minutes: int) -> pd.DataFrame:
    g = m1.groupby(m1["time"] // (minutes * 60) * (minutes * 60))
    return pd.DataFrame({"time": g["time"].first().index, "open": g["open"].first().values,
                         "high": g["high"].max().values, "low": g["low"].min().values,
                         "close": g["close"].last().values, "spread": g["spread"].max().values})


def signals(m1: pd.DataFrame, lookback: int, atr_mult: float, min_spread_mult: float, point: float,
            horizon: int, mode: str = "structure", swing_k: int = 3) -> pd.DataFrame:
    m5 = resample(m1, 5)
    m15 = resample(m1, 15)
    m15["atr"] = atr(m15["high"].values, m15["low"].values, m15["close"].values, 14)
    m15["avail"] = m15["time"] + 15 * 60                      # ATR known only once the M15 bar is closed
    m5["close_t"] = m5["time"] + 5 * 60
    m5 = pd.merge_asof(m5.sort_values("close_t"), m15[["avail", "atr"]].sort_values("avail"),
                       left_on="close_t", right_on="avail", direction="backward")
    c = m5["close"].values
    cands = []                                                # (index, side, broken level, kind)
    if mode == "structure":
        st = analyze(m5[["time", "open", "high", "low", "close"]].reset_index(drop=True), k=swing_k)
        for ev in st["events"]:
            cands.append((ev.idx, 1 if ev.direction == "bull" else -1, ev.price, ev.kind))
    else:
        hh = m5["high"].shift(1).rolling(lookback).max().values
        ll = m5["low"].shift(1).rolling(lookback).min().values
        last = {1: -10 ** 9, -1: -10 ** 9}
        for i in range(lookback + 1, len(m5)):
            side = 1 if c[i] > hh[i] else (-1 if c[i] < ll[i] else 0)
            if side == 0 or i - last[side] <= lookback:
                continue
            last[side] = i
            cands.append((i, side, hh[i] if side == 1 else ll[i], "range"))
    t1 = m1["time"].values
    h1, l1, c1 = m1["high"].values, m1["low"].values, m1["close"].values
    sp1 = m1["spread"].values * point
    rows = []
    for i, side, level, kind in cands:
        a = m5["atr"].iat[i]
        if not np.isfinite(a) or a <= 0:
            continue
        spread = m5["spread"].iat[i] * point
        entry = c[i] + spread if side == 1 else c[i]
        risk = max(atr_mult * a, min_spread_mult * spread)
        start = np.searchsorted(t1, m5["close_t"].iat[i])
        end = np.searchsorted(t1, m5["close_t"].iat[i] + horizon * 60)
        if end - start < 5:
            continue
        if side == 1:                                         # long exits at bid
            adverse = (entry - l1[start:end]) / risk
            fav = (h1[start:end] - entry) / risk
        else:                                                 # short exits at ask
            adverse = (h1[start:end] + sp1[start:end] - entry) / risk
            fav = (entry - (l1[start:end] + sp1[start:end])) / risk
        hit = np.nonzero(adverse >= 1.0)[0]
        stop_at = hit[0] if len(hit) else None
        mfe = float(fav[:stop_at].max()) if stop_at is None or stop_at > 0 else 0.0
        mfe = max(mfe, 0.0)
        # back inside: an M5 close back through the broken level within 15 minutes (3 M5 bars)
        nxt = c[i + 1:i + 4]
        back = bool(np.any(nxt < level)) if side == 1 else bool(np.any(nxt > level))
        rows.append({"time": int(m5["close_t"].iat[i]), "side": "long" if side == 1 else "short", "kind": kind,
                     "risk": risk, "mfe_r": mfe, "stopped": stop_at is not None,
                     "minutes_to_stop": int(stop_at + 1) if stop_at is not None else None, "back_inside": back})
    df = pd.DataFrame(rows)
    if len(df):
        ts = pd.to_datetime(df["time"], unit="s", utc=True)
        df["hour_il"] = ts.dt.tz_convert(TZ).dt.hour
        df["hour_utc"] = ts.dt.hour
        df["weekday"] = ts.dt.tz_convert(TZ).dt.day_name()
        df["strong"] = df["mfe_r"] >= 1.4
        df["very_strong"] = df["mfe_r"] >= 2.0
        df["fake"] = df["stopped"] & (df["mfe_r"] < 0.5)
    return df


def by_hour(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("hour_il")
    t = pd.DataFrame({
        "hour_utc": g["hour_utc"].agg(lambda s: s.mode().iat[0]),
        "signals": g.size(),
        "strong_1.4R_%": (g["strong"].mean() * 100).round(1),
        "very_strong_2R_%": (g["very_strong"].mean() * 100).round(1),
        "fake_%": (g["fake"].mean() * 100).round(1),
        "back_inside_15m_%": (g["back_inside"].mean() * 100).round(1),
        "median_mfe_r": g["mfe_r"].median().round(2),
    })
    t["strong_minus_fake"] = (t["strong_1.4R_%"] - t["fake_%"]).round(1)
    return t


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--out", default="reports/breakout_hours")
    ap.add_argument("--mode", choices=["structure", "range"], default="structure",
                    help="structure = M5 BOS/CHoCH of the system's swing structure (default); range = N-bar high/low")
    ap.add_argument("--swing-k", type=int, default=3, help="fractal swing strength (bars each side), as the system")
    ap.add_argument("--lookback", type=int, default=12, help="range mode only")
    ap.add_argument("--atr-mult", type=float, default=1.5)
    ap.add_argument("--min-spread-mult", type=float, default=5.0)
    ap.add_argument("--horizon", type=int, default=240, help="minutes to follow each breakout")
    ap.add_argument("--point", type=float, default=0.01, help="price of one spread point (XAUUSD 2 digits: 0.01)")
    ap.add_argument("--min-signals", type=int, default=20, help="hours with fewer signals are marked thin")
    a = ap.parse_args()
    m1 = read_m1_csv(a.csv)
    src = next((ln[1:].strip() for ln in open(a.csv, encoding="utf-8") if ln.startswith("#")), "unknown source")
    df = signals(m1, a.lookback, a.atr_mult, a.min_spread_mult, a.point, a.horizon, a.mode, a.swing_k)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if df.empty:
        print("no breakouts found")
        return 1
    df.to_csv(out / "breakouts.csv", index=False)
    t = by_hour(df)
    t["thin"] = t["signals"] < a.min_signals
    t.to_csv(out / "by_hour.csv")
    kinds = {k: by_hour(g) for k, g in df.groupby("kind")} if a.mode == "structure" else {}
    for k, tk in kinds.items():
        tk.to_csv(out / f"by_hour_{k}.csv")
    d0, d1 = (pd.to_datetime(m1["time"].iloc[[0, -1]], unit="s", utc=True).dt.strftime("%Y-%m-%d")).tolist()
    tot = {"signals": len(df), "strong_1.4R_%": round(float(df["strong"].mean()) * 100, 1),
           "very_strong_2R_%": round(float(df["very_strong"].mean()) * 100, 1),
           "fake_%": round(float(df["fake"].mean()) * 100, 1), "back_inside_15m_%": round(float(df["back_inside"].mean()) * 100, 1)}
    lines = [f"# M5 breakouts by hour - {Path(a.csv).stem}", "", f"Data: {src}, {d0} -> {d1}. " +
             (f"Signals: M5 structure BOS/CHoCH (swing k={a.swing_k}), " if a.mode == "structure" else f"Signals: {a.lookback}-bar range, ") +
             f"1R = {a.atr_mult} x ATR14(M15) (min {a.min_spread_mult} x spread), "
             f"followed {a.horizon} min on M1, stop counted first when ambiguous.", "",
             f"All hours: {tot}", "", t.to_string()]
    for k, tk in kinds.items():
        g = df[df["kind"] == k]
        lines += ["", f"## {k}: {len(g)} signals, strong {g['strong'].mean() * 100:.1f}%, fake {g['fake'].mean() * 100:.1f}%",
                  tk.to_string()]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
