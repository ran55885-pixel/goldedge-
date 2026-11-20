"""Backtest report: equity_curve.png, trades.csv, report.json and report.md with stats per setup /
session / symbol / timeframe / exit type, trades per day per symbol, in-sample vs out-of-sample split,
walk-forward windows, time-target comparison and the RR-floor assertion."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from aurum_nexus.bots.analyst import group_stats, stats  # noqa: E402

TRADE_COLS = ["id", "symbol", "direction", "setup", "timeframe", "session", "regime", "score", "open_ts", "close_ts",
              "entry", "initial_stop", "tp1", "tp2", "volume", "exit_price", "r_multiple", "mfe_r", "mae_r", "pnl",
              "commission", "pnl_net", "exit_type", "exit_reason"]


def _iso(ts):
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if ts else ""


def trades_per_day(trades: list[dict], start: float, end: float) -> dict:
    days = max(1.0, (end - start) / 86400.0 * 5 / 7)  # trading days (approx., excludes weekends)
    by = defaultdict(int)
    for t in trades:
        by[t["symbol"]] += 1
    return {s: round(n / days, 2) for s, n in by.items()}


def split_stats(trades: list[dict], start: float, end: float, is_frac: float) -> dict:
    cut = start + (end - start) * is_frac
    return {"cut": _iso(cut), "in_sample": stats([t for t in trades if t["open_ts"] < cut]),
            "out_of_sample": stats([t for t in trades if t["open_ts"] >= cut])}


def windows(trades: list[dict], start: float, end: float, test_days: float) -> list[dict]:
    out = []
    w = test_days * 86400
    t = start
    while t < end:
        seg = [x for x in trades if t <= x["open_ts"] < t + w]
        out.append({"from": _iso(t), "to": _iso(min(t + w, end)), **stats(seg)})
        t += w
    return out


def summarize(res: dict, cfg) -> dict:
    tr = res["trades"]
    start, end = res["start"], res["end"]
    return {
        "period": {"from": _iso(start), "to": _iso(end)}, "symbols": res["symbols"], "step_minutes": res["step"],
        "balance": res["balance"], "final_equity": round(res["final_equity"], 2),
        "overall": stats(tr), "by_setup": group_stats(tr, "setup"), "by_session": group_stats(tr, "session"),
        "by_symbol": group_stats(tr, "symbol"), "by_timeframe": group_stats(tr, "timeframe"),
        "by_exit_type": group_stats(tr, "exit_type"), "trades_per_day": trades_per_day(tr, start, end),
        "signals": len(res["signals"]), "qualified_signals": sum(1 for s in res["signals"] if s["score"] >= cfg.min_score),
        "is_oos": split_stats(tr, start, end, float(cfg.get("backtest.is_split", 0.7))),
        "walk_forward_windows": windows(tr, start, end, float(cfg.get("backtest.walk_forward.test_days", 5))),
        "trade_manager_fired": res["fired"],
        "shadow_audit": _shadow(res["shadows"]),
        "min_planned_rr": res["min_planned_rr"], "rr_floor_ok": res["min_planned_rr"] is None or res["min_planned_rr"] >= cfg.min_rr - 1e-6,
        "costs": {"commission_per_lot": res["commission_per_lot"], "slippage_points": res["slippage_points"],
                  "spread": "CSV spread column or backtest.default_spread_points"},
        "volume_available": res["has_volume"], "runtime_s": res["runtime_s"],
        "assumptions": [
            "Identical live pipeline (Engine) on historical M1; cycle 6 s after each M1 close.",
            "Intra-bar path O -> adverse extreme -> favourable extreme -> C (SL assumed before TP in the same bar).",
            "Gaps through the stop fill at the bar open; TP/limit exits fill at their price.",
            "Spread from CSV (points) else default; commission per lot round-turn; stop/market slippage in points.",
            "R multiples are price-based (spread & slippage included); commission is in the money P/L (pnl_net).",
            "No historical news/COT/macro/sentiment/DOM in backtests (filters inactive unless manual blackouts).",
        ],
    }


def _shadow(rows: list[dict]) -> dict:
    by = defaultdict(list)
    for r in rows:
        if r.get("shadow_r") is not None:
            by[r["rule"]].append(float(r["exit_r"]) - float(r["shadow_r"]))
    return {k: {"n": len(v), "avg_saved_r": round(sum(v) / len(v), 3)} for k, v in by.items()}


def equity_png(res: dict, path: Path, title: str = "Aurum Nexus backtest") -> None:
    eq = res["equity"]
    fig, ax = plt.subplots(figsize=(11, 4.2), dpi=110)
    fig.patch.set_facecolor("#060b18")
    ax.set_facecolor("#060b18")
    if eq:
        x = [datetime.fromtimestamp(t, tz=timezone.utc) for t, _ in eq]
        y = [v for _, v in eq]
        ax.plot(x, y, color="#39ff14", lw=1.8)
        ax.fill_between(x, y, min(y), color="#39ff14", alpha=0.12)
        ax.annotate(f"{y[-1]:,.2f}", (x[-1], y[-1]), color="#e8f7ff", fontsize=9, ha="right", va="bottom")
    ax.set_title(title, color="#9fe8ff")
    ax.tick_params(colors="#7fa7c9")
    for s in ax.spines.values():
        s.set_color("#1e3a5f")
    ax.grid(color="#12243d", lw=0.6)
    fig.tight_layout()
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)


def write_report(res: dict, cfg, out_dir: str | Path, extra: dict | None = None, label: str = "") -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary = summarize(res, cfg)
    if extra:
        summary.update(extra)
    rows = []
    for t in res["trades"]:
        r = {k: t.get(k) for k in TRADE_COLS}
        r["open_time"] = _iso(t["open_ts"])
        r["close_time"] = _iso(t.get("close_ts"))
        rows.append(r)
    pd.DataFrame(rows, columns=["open_time", "close_time"] + TRADE_COLS).to_csv(out / "trades.csv", index=False)
    equity_png(res, out / "equity_curve.png", f"Aurum Nexus backtest {label}".strip())
    (out / "report.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    (out / "report.md").write_text(to_markdown(summary, label), encoding="utf-8")
    return summary


def _fmt(s: dict) -> str:
    if not s or not s.get("trades"):
        return "0 trades"
    pf = s["profit_factor"]
    pf = "inf" if pf == float("inf") else (f"{pf:.2f}" if pf is not None else "n/a")
    return (f"{s['trades']} trades · WR {s['win_rate']}% · exp {s['expectancy_r']:+.3f}R · PF {pf} · "
            f"maxDD {s['max_dd_r']}R · total {s['total_r']:+.2f}R")


def to_markdown(s: dict, label: str = "") -> str:
    L = [f"# Aurum Nexus backtest report {label}".strip(), "",
         "> Historical simulation. Past results do not predict future results. No profit promise.", "",
         f"Period: {s['period']['from']} → {s['period']['to']} UTC · symbols {', '.join(s['symbols'])} · "
         f"step {s['step_minutes']}m", "",
         f"Balance {s['balance']:,.2f} → {s['final_equity']:,.2f}", "",
         f"**Overall:** {_fmt(s['overall'])}", "",
         f"RR floor check: min planned RR = {s['min_planned_rr']} → {'OK' if s['rr_floor_ok'] else 'VIOLATION'}", "",
         f"In-sample (until {s['is_oos']['cut']}): {_fmt(s['is_oos']['in_sample'])}  ",
         f"Out-of-sample: {_fmt(s['is_oos']['out_of_sample'])}", "", "## Trades per day per symbol", ""]
    for k, v in s["trades_per_day"].items():
        L.append(f"- {k}: {v}")
    for key, title in (("by_setup", "Setup"), ("by_session", "Session"), ("by_symbol", "Symbol"),
                       ("by_timeframe", "Timeframe"), ("by_exit_type", "Exit type")):
        L += ["", f"## By {title}", "", "| key | result |", "|---|---|"]
        for k, v in s[key].items():
            L.append(f"| {k} | {_fmt(v)} |")
    L += ["", "## Walk-forward windows (out-of-sample segments)", "", "| from | to | result |", "|---|---|---|"]
    for w in s["walk_forward_windows"]:
        L.append(f"| {w['from']} | {w['to']} | {_fmt(w)} |")
    if s.get("time_target_comparison"):
        L += ["", "## Time targets: with vs without + N/T1/T2 grid", "", "| variant | result |", "|---|---|"]
        for k, v in s["time_target_comparison"].items():
            L.append(f"| {k} | {_fmt(v)} |")
    L += ["", "## Trade Manager rules fired", ""] + [f"- {k}: {v}" for k, v in s["trade_manager_fired"].items()]
    L += ["", "## Assumptions", ""] + [f"- {a}" for a in s["assumptions"]]
    return "\n".join(L) + "\n"
