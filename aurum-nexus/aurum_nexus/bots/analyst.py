"""ANALYST - performance coach.

Tagged trade journal; stats per setup, session, symbol, timeframe and exit type (win rate, expectancy R,
profit factor, max DD, average R); early-exit & time-target audits vs shadow positions.
Adaptive setup weighting ONLY after a minimum sample (>=15), bounded (±15 points).
Persistently negative setups are disabled temporarily with a 48h probation - safety rules are never
touched. Seasonality learning (symbol x setup x hour x weekday) with shrinkage to the global mean,
bounded modifier and heatmap. Opportunity-frequency monitor vs target (>=3 trades/day per symbol):
after 5+ days below target it SUGGESTS (never auto-applies) playbooks/timeframes/symbols to enable.
Quality is never lowered to reach a quota.
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from aurum_nexus.bots.base import Bot


def stats(trades: list[dict]) -> dict:
    rs = [float(t.get("r_multiple") or 0.0) for t in trades if t.get("r_multiple") is not None]
    pnl = [float(t.get("pnl") or 0.0) for t in trades if t.get("r_multiple") is not None]
    n = len(rs)
    if n == 0:
        return {"trades": 0, "win_rate": None, "expectancy_r": None, "profit_factor": None, "avg_r": None,
                "max_dd_r": None, "total_r": 0.0, "pnl": 0.0, "avg_win_r": None, "avg_loss_r": None}
    arr = np.array(rs)
    wins = arr[arr > 0]
    losses = arr[arr < 0]
    gp, gl = float(wins.sum()), float(-losses.sum())
    cum = np.cumsum(arr)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    dd = float((peak - cum).max()) if n else 0.0
    return {"trades": n, "win_rate": round(float((arr > 0).mean()) * 100, 1), "expectancy_r": round(float(arr.mean()), 3),
            "profit_factor": round(gp / gl, 2) if gl > 0 else (None if gp == 0 else float("inf")),
            "avg_r": round(float(arr.mean()), 3), "max_dd_r": round(dd, 2), "total_r": round(float(arr.sum()), 2),
            "pnl": round(float(sum(pnl)), 2), "avg_win_r": round(float(wins.mean()), 2) if len(wins) else None,
            "avg_loss_r": round(float(losses.mean()), 2) if len(losses) else None}


def group_stats(trades: list[dict], key: str) -> dict:
    g = defaultdict(list)
    for t in trades:
        g[t.get(key) or "n/a"].append(t)
    return {k: stats(v) for k, v in sorted(g.items())}


class Analyst(Bot):
    name = "ANALYST"
    color = "#4dabff"
    job = "Journal, stats, adaptive weights, seasonality, opportunity monitor"

    def __init__(self, bus, cfg, book, storage, clock=None):
        super().__init__(bus, cfg, clock)
        self.ac = cfg.get("analyst")
        self.book = book
        self.storage = storage
        self.adjust: dict[str, float] = {}
        self.disabled: dict[str, float] = {}      # setup -> disabled until ts
        self.probation_since: dict[str, float] = {}
        self.season: dict[tuple, tuple[float, int]] = {}
        self.global_mean = 0.0
        self.report: dict = {}
        self.warnings: list[str] = []
        self.suggestions: list[str] = []
        self._last = 0.0

    def config_view(self) -> dict:
        return dict(self.ac)

    # ------------------------------------------------------------------ query API used by Signal/Guardian
    def setup_adjust(self, setup: str) -> float:
        return float(self.adjust.get(setup, 0.0))

    def setup_disabled(self, setup: str) -> bool:
        until = self.disabled.get(setup)
        return bool(until and self.clock() < until)

    def seasonality_modifier(self, symbol: str, setup: str, hour: int, weekday: int) -> float:
        rec = self.season.get((symbol, setup, hour, weekday))
        if not rec:
            return 0.0
        est, n = rec
        cap = float(self.ac["seasonality_cap"])
        return float(max(-cap, min(cap, (est - self.global_mean) * 10.0)))

    # ------------------------------------------------------------------ recompute
    def refresh(self, now: float | None = None, force: bool = False, contexts_symbols: list[str] | None = None) -> dict:
        now = now if now is not None else self.clock()
        if not force and now - self._last < 30:
            return self.report
        self._last = now
        trades = self.book.closed()
        trades = [t for t in trades if t.get("r_multiple") is not None]
        rep = {"overall": stats(trades)}
        for key in ("setup", "session", "symbol", "timeframe", "exit_type", "mode", "account"):
            rep[f"by_{key}"] = group_stats(trades, key)
        rep["shadow_audit"] = self._shadow_audit()
        self._adaptive(trades, now)
        self._seasonality(trades)
        rep["adaptive"] = {"adjust": self.adjust, "disabled": {k: v for k, v in self.disabled.items() if v > now}}
        rep["frequency"] = self._frequency(now, contexts_symbols or [])
        rep["heatmaps"] = self.heatmaps(trades)
        rep["warnings"] = self.warnings
        rep["suggestions"] = self.suggestions
        self.report = rep
        ov = rep["overall"]
        self.set_status(f"{ov['trades']} closed · exp {ov['expectancy_r'] if ov['expectancy_r'] is not None else 'n/a'}R"
                        f" · WR {ov['win_rate'] if ov['win_rate'] is not None else 'n/a'}%")
        self.pulse(ov["expectancy_r"] or 0.0)
        return rep

    def _shadow_audit(self) -> dict:
        rows = self.storage.shadows("closed")
        by = defaultdict(list)
        for r in rows:
            by[r["rule"]].append(float(r["exit_r"] or 0) - float(r["shadow_r"] or 0))
        return {k: {"n": len(v), "avg_saved_r": round(float(np.mean(v)), 3), "helped_pct": round(100 * float(np.mean(np.array(v) >= 0)), 1)}
                for k, v in by.items()}

    def _adaptive(self, trades: list[dict], now: float) -> None:
        min_n = int(self.ac["min_sample"])
        cap = float(self.ac["max_weight_adjust"])
        by = defaultdict(list)
        for t in trades:
            by[t.get("setup")].append(t)
        for setup, ts in by.items():
            since = self.probation_since.get(setup, 0)
            sample = [t for t in ts if (t.get("close_ts") or 0) >= since]
            if len(sample) < min_n:
                self.adjust.pop(setup, None)
                continue
            exp = float(np.mean([t["r_multiple"] for t in sample]))
            new = max(-cap, min(cap, round(exp * 20.0, 1)))
            if self.adjust.get(setup) != new:
                self.adjust[setup] = new
                self.log("ANALYST", f"adaptive weight {setup}: {new:+.1f} (expectancy {exp:+.2f}R, n={len(sample)})")
            if len(sample) >= int(self.ac["disable_min_trades"]) and exp < float(self.ac["disable_expectancy_r"]):
                if not self.setup_disabled(setup):
                    until = now + 3600 * float(self.ac["probation_hours"])
                    self.disabled[setup] = until
                    self.probation_since[setup] = until
                    self.log("ANALYST", f"setup {setup} DISABLED for {self.ac['probation_hours']}h probation "
                             f"(expectancy {exp:+.2f}R over {len(sample)} trades) - safety rules untouched")

    def _seasonality(self, trades: list[dict]) -> None:
        if not trades:
            return
        rs = [t["r_multiple"] for t in trades]
        self.global_mean = float(np.mean(rs))
        m = float(self.ac["seasonality_shrinkage"])
        cells = defaultdict(list)
        for t in trades:
            dt = datetime.fromtimestamp(t["open_ts"], tz=timezone.utc)
            cells[(t["symbol"], t["setup"], dt.hour, dt.weekday())].append(t["r_multiple"])
        self.season = {k: ((len(v) * float(np.mean(v)) + m * self.global_mean) / (len(v) + m), len(v))
                       for k, v in cells.items()}

    def heatmaps(self, trades: list[dict]) -> dict:
        """symbol -> 7x24 grid of shrunk expectancy (None where no trades)."""
        m = float(self.ac["seasonality_shrinkage"])
        out = {}
        g = defaultdict(lambda: defaultdict(list))
        for t in trades:
            dt = datetime.fromtimestamp(t["open_ts"], tz=timezone.utc)
            g[t["symbol"]][(dt.weekday(), dt.hour)].append(t["r_multiple"])
        for sym, cells in g.items():
            grid = [[None] * 24 for _ in range(7)]
            for (wd, h), v in cells.items():
                grid[wd][h] = {"est": round((len(v) * float(np.mean(v)) + m * self.global_mean) / (len(v) + m), 3),
                               "n": len(v)}
            out[sym] = grid
        return out

    def _frequency(self, now: float, symbols: list[str]) -> dict:
        target = float(self.ac["target_trades_per_day"])
        days = 10
        since = now - days * 86400
        sig = self.storage.query("SELECT symbol, ts, score FROM signals WHERE ts>=?", (since,))
        trades = self.storage.query("SELECT symbol, open_ts FROM trades WHERE open_ts>=?", (since,))
        out = {}
        warns, sugg = [], []
        min_score = self.cfg.min_score
        for s in symbols:
            per_day_sig = defaultdict(int)
            per_day_tr = defaultdict(int)
            for r in sig:
                if r["symbol"] == s and (r["score"] or 0) >= min_score:
                    per_day_sig[int(r["ts"] // 86400)] += 1
            for r in trades:
                if r["symbol"] == s:
                    per_day_tr[int(r["open_ts"] // 86400)] += 1
            active_days = sorted(set(per_day_sig) | set(per_day_tr))
            n_days = max(1, len(active_days))
            avg_sig = sum(per_day_sig.values()) / n_days
            avg_tr = sum(per_day_tr.values()) / n_days
            below = sum(1 for d in active_days if per_day_tr.get(d, 0) < target)
            out[s] = {"avg_qualified_signals_per_day": round(avg_sig, 2), "avg_trades_per_day": round(avg_tr, 2),
                      "target": target, "days_observed": len(active_days), "days_below_target": below}
            if len(active_days) >= int(self.ac["frequency_warn_days"]) and below >= int(self.ac["frequency_warn_days"]):
                warns.append(f"{s}: {avg_tr:.1f} trades/day vs target {target:.0f} for {below} days")
                sugg.append(f"{s}: consider enabling more playbooks / M1 entries / extra sessions in the profile, or "
                            f"adding correlated-but-distinct symbols - suggestion only, quality thresholds unchanged")
        self.warnings, self.suggestions = warns, sugg
        return out
