"""Walk-forward optimiser (mt5sys optimize --symbol X).

For each window: grid-search BOUNDED profile parameters on the training period, pick the best by an
objective that penalises few trades and high drawdown, then evaluate that choice on the following
out-of-sample test period. The proposal = the parameters chosen on the latest training window, reported
with the aggregated out-of-sample result of every window. It never touches min_rr, min_score or Guardian
rules (the Strategist rejects such keys) and is written as a proposal + proposed profile YAML.
"""
from __future__ import annotations

import copy
import itertools
import json
import time
from pathlib import Path

import yaml

from aurum_nexus.backtest.engine import run_backtest
from aurum_nexus.bots.analyst import stats
from aurum_nexus.bots.strategist import BOUNDS, _set, clamp_profile, default_profile

DEFAULT_GRID = {
    "stops.atr_buffer_mult": [0.25, 0.45],
    "targets.tp2_rr_default": [1.6, 2.4],
    "time_targets.t2_mult": [1.5, 2.5],
}


def objective(st: dict, min_trades: int = 8, max_dd_r: float = 6.0) -> float:
    n = st.get("trades") or 0
    if n < 3:
        return -1.0
    exp = st["expectancy_r"]
    pen_n = min(1.0, n / float(min_trades))
    pen_dd = 0.05 * max(0.0, (st.get("max_dd_r") or 0.0) - max_dd_r)
    return exp * pen_n - pen_dd


def _check_grid(grid: dict) -> dict:
    for k, vals in grid.items():
        if k not in BOUNDS and not k.startswith("playbooks."):
            raise ValueError(f"parameter {k} is not optimisable (allowed: {sorted(BOUNDS)})")
        lo, hi = BOUNDS.get(k, BOUNDS["playbooks.*.weight"] if k.startswith("playbooks.") else (None, None))
        for v in vals:
            if lo is not None and not (lo <= v <= hi):
                raise ValueError(f"{k}={v} outside bounds [{lo},{hi}]")
    return grid


def walk_forward(cfg, data: dict, symbol: str, grid: dict | None = None, train_days: float | None = None,
                 test_days: float | None = None, step: int = 5, warmup_days: float = 2.0, log=print) -> dict:
    grid = _check_grid(grid or DEFAULT_GRID)
    train_days = float(train_days or cfg.get("backtest.walk_forward.train_days", 20))
    test_days = float(test_days or cfg.get("backtest.walk_forward.test_days", 5))
    syms = [symbol] + [s for s in data if s != symbol]
    t_all = data[symbol]["time"]
    first = float(t_all.iloc[0]) + warmup_days * 86400
    last = float(t_all.iloc[-1])
    combos = [dict(zip(grid, vals)) for vals in itertools.product(*grid.values())]
    windows = []
    t = first
    while t + (train_days + test_days) * 86400 <= last + 60:
        windows.append((t, t + train_days * 86400, t + (train_days + test_days) * 86400))
        t += test_days * 86400
    if not windows:
        raise ValueError(f"not enough data for one window ({train_days}+{test_days} days after {warmup_days}d warm-up)")
    results = []
    oos_trades = []
    t0 = time.time()
    for wi, (a, b, c) in enumerate(windows):
        best, best_obj, scores = None, -1e9, []
        for combo in combos:
            res = run_backtest(cfg, data, syms, start=a, end=b, step=step, warmup_days=0,
                               profile_overrides={symbol: combo})
            st = stats([x for x in res["trades"] if x["symbol"] == symbol])
            ob = objective(st)
            scores.append({"params": combo, "train": st, "objective": round(ob, 4)})
            if ob > best_obj:
                best, best_obj = combo, ob
        res = run_backtest(cfg, data, syms, start=b, end=c, step=step, warmup_days=0,
                           profile_overrides={symbol: best})
        tr = [x for x in res["trades"] if x["symbol"] == symbol]
        oos_trades += tr
        results.append({"window": wi, "train_from": a, "train_to": b, "test_to": c, "chosen": best,
                        "train_objective": round(best_obj, 4), "oos": stats(tr), "grid": scores})
        log(f"window {wi + 1}/{len(windows)}: chosen {best} -> OOS {stats(tr)}")
    proposal = results[-1]["chosen"]
    return {"symbol": symbol, "grid": grid, "windows": results, "oos_overall": stats(oos_trades),
            "proposal": proposal, "train_days": train_days, "test_days": test_days, "step": step,
            "runtime_s": round(time.time() - t0, 1)}


def write_optimize_report(res: dict, strategist, out_dir: str | Path, register: bool = True) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sym = res["symbol"]
    base = copy.deepcopy(strategist.profile(sym)) if strategist else default_profile(sym)
    prop = copy.deepcopy(base)
    for k, v in res["proposal"].items():
        _set(prop, k, v)
    prop, warns = clamp_profile(prop)
    prop["oos_stats"] = res["oos_overall"]
    prop["notes"] = (base.get("notes", "") + f" | walk-forward proposal {time.strftime('%Y-%m-%d')}").strip()
    (out / f"proposed_{sym}.yaml").write_text(yaml.safe_dump(prop, sort_keys=False, allow_unicode=True), encoding="utf-8")
    (out / "optimize_report.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    oos = res["oos_overall"]
    md = [f"# Walk-forward optimisation {sym}", "", "> Out-of-sample results are historical, not a promise.", "",
          f"Grid: {json.dumps(res['grid'])}", f"Windows: {len(res['windows'])} (train {res['train_days']}d / test "
          f"{res['test_days']}d, step {res['step']}m)", "",
          f"**OOS overall:** {oos}", "", f"**Proposal:** {res['proposal']}", ""]
    for w in res["windows"]:
        md.append(f"- window {w['window']}: chosen {w['chosen']} · train obj {w['train_objective']} · OOS {w['oos']}")
    if warns:
        md += ["", "Clamped: " + "; ".join(warns)]
    (out / "optimize_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    reg = None
    if register and strategist is not None:
        reason = (f"walk-forward {len(res['windows'])} windows, OOS exp "
                  f"{oos['expectancy_r'] if oos['expectancy_r'] is not None else 'n/a'}R over {oos['trades']} trades")
        reg = strategist.propose(sym, res["proposal"], reason, "walk-forward optimiser",
                                 {"oos": oos, "report": str(out)})
    return {"out": str(out), "proposal": res["proposal"], "registered": reg, "warnings": warns}
