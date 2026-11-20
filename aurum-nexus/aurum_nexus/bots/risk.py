"""RISK - risk manager & position sizer (the Trade Manager lives in trade_manager.py under this bot).

Lot size = risk money / (SL distance in ticks x tick value), using the broker's trade_tick_value /
trade_tick_size, respecting volume_min / volume_step / volume_max and NEVER rounding up beyond budget
(if the budget is below volume_min the trade is skipped). Correct for XAUUSD and all FX because tick
value is expressed in the account currency.

RR floor (hard, >= 1.0): measured from the REALISTIC entry (ask for longs / bid for shorts, i.e. spread
included) to SL, and from that entry to TP1, final TP and the next opposing obstacle. If the real
obstacle is closer than min_rr x R the trade is skipped - TP is never stretched. Re-checked immediately
before sending and after the fill.
"""
from __future__ import annotations

import math

from aurum_nexus.bots.base import Bot
from aurum_nexus.models import Candidate, SymbolSpec


def step_decimals(step: float) -> int:
    s = f"{step:.10f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s else 0


def lot_size(risk_money: float, entry: float, stop: float, spec: SymbolSpec) -> tuple[float, str]:
    """Largest lot size whose loss at the stop does not exceed risk_money. Returns (lots, note)."""
    dist = abs(entry - stop)
    if dist <= 0 or spec.tick_size <= 0 or spec.tick_value <= 0:
        return 0.0, "invalid stop distance or tick data"
    loss_per_lot = (dist / spec.tick_size) * spec.tick_value
    raw = risk_money / loss_per_lot
    step = spec.volume_step or 0.01
    lots = math.floor(raw / step + 1e-9) * step
    lots = round(lots, step_decimals(step))
    if lots > spec.volume_max:
        lots = spec.volume_max
    if lots < spec.volume_min - 1e-12:
        need = spec.volume_min * loss_per_lot
        return 0.0, f"budget {risk_money:.2f} < risk of volume_min {spec.volume_min} ({need:.2f}) - skipped (never round up)"
    return lots, f"{lots} lots (loss at SL {lots * loss_per_lot:.2f} <= budget {risk_money:.2f})"


def loss_at_stop(lots: float, entry: float, stop: float, spec: SymbolSpec) -> float:
    return abs(entry - stop) / spec.tick_size * spec.tick_value * lots


def rebase_tp1(direction: str, entry: float, stop: float, tp1: float, tp1_rr: float, min_rr: float) -> float:
    """TP1 is a managed partial: re-derive it from the realistic/actual entry so spread or slippage can never
    push it below the floor. Final TP and the opposing obstacle are NOT moved (they stay hard checks)."""
    sign = 1 if direction == "long" else -1
    risk = (entry - stop) * sign
    if risk <= 0:
        return tp1
    want = entry + sign * max(tp1_rr, min_rr) * risk
    return want if (want - tp1) * sign > 0 else tp1


RR_EPS = 1e-6  # float tolerance: a target placed exactly at 1R must pass the 1R floor


def rr_check(direction: str, entry: float, stop: float, tp1: float, tp2: float, obstacle: float | None,
             min_rr: float) -> tuple[bool, dict, str]:
    sign = 1 if direction == "long" else -1
    risk = (entry - stop) * sign
    if risk <= 0:
        return False, {}, "invalid: stop on the wrong side of entry"
    rr = {"tp1": (tp1 - entry) * sign / risk, "final": (tp2 - entry) * sign / risk}
    if obstacle is not None:
        rr["obstacle"] = (obstacle - entry) * sign / risk
    worst_key = min(rr, key=rr.get)
    if rr[worst_key] < min_rr - RR_EPS:
        return False, rr, f"RR {rr[worst_key]:.2f} < {min_rr:.1f} ({worst_key})"
    return True, rr, "ok"


class RiskManager(Bot):
    name = "RISK"
    color = "#ff3df2"
    job = "Position sizing, 1:1 floor, size reduction, profit lock + Trade Manager"

    def __init__(self, bus, cfg, clock=None):
        super().__init__(bus, cfg, clock)
        self.rc = cfg.get("risk")
        self.locks: dict[str, str] = {}

    def config_view(self) -> dict:
        return {k: self.rc[k] for k in ("min_rr", "min_score", "default_risk_pct", "tp1_rr", "tp1_fraction",
                                        "atr_buffer_mult", "daily_profit_lock", "loss_streak_reduction",
                                        "drawdown_reduction", "equity_curve_filter")}

    # ------------------------------------------------------------------ size factors
    def size_factor(self, closed_trades: list[dict], equity: float | None, peak_equity: float | None) -> tuple[float, list[str]]:
        """Multiplicative size reduction after losses, in drawdown and by the equity-curve filter."""
        f = 1.0
        notes = []
        ls = self.rc["loss_streak_reduction"]
        streak = 0
        for t in closed_trades:  # newest first
            if (t.get("r_multiple") or 0) < 0:
                streak += 1
            else:
                break
        if streak >= int(ls["after_losses"]):
            f *= float(ls["factor"])
            notes.append(f"{streak} losses x{ls['factor']}")
        if equity and peak_equity and peak_equity > 0:
            dd = 100.0 * (peak_equity - equity) / peak_equity
            for step in sorted(self.rc["drawdown_reduction"], key=lambda s: -s["dd_pct"]):
                if dd >= float(step["dd_pct"]):
                    f *= float(step["factor"])
                    notes.append(f"drawdown {dd:.1f}% x{step['factor']}")
                    break
        ec = self.rc["equity_curve_filter"]
        if ec.get("enabled"):
            last = [t.get("r_multiple") or 0.0 for t in closed_trades[: int(ec["lookback_trades"])]]
            if len(last) >= int(ec["lookback_trades"]) and sum(last) < 0:
                f *= float(ec["factor"])
                notes.append(f"equity curve {sum(last):+.1f}R over {len(last)} x{ec['factor']}")
        f = max(float(self.rc["min_size_factor"]), f)
        return f, notes

    def profit_lock(self, account: str, realized_r_seq: list[float]) -> tuple[bool, str]:
        """Stop new entries after +lock_at_r for the day or after giving back giveback_pct of the day's peak."""
        pl = self.rc["daily_profit_lock"]
        if not pl.get("enabled"):
            return False, ""
        cum = peak = 0.0
        for r in realized_r_seq:
            cum += r
            peak = max(peak, cum)
        if cum >= float(pl["lock_at_r"]):
            return True, f"daily profit lock: +{cum:.1f}R >= +{pl['lock_at_r']}R"
        if peak >= float(pl["min_peak_r"]) and cum <= peak * (1 - float(pl["giveback_pct"]) / 100.0):
            return True, f"daily profit lock: gave back {100 * (peak - cum) / peak:.0f}% of +{peak:.1f}R peak"
        return False, ""

    # ------------------------------------------------------------------ plan
    def plan(self, c: Candidate, account_name: str, risk_pct: float, equity: float, spec: SymbolSpec,
             bid: float, ask: float, offset: float, factor: float, profile: dict) -> tuple[dict | None, str]:
        """Re-base the candidate to this account's broker price and size it. Returns (plan, reason)."""
        sign = 1 if c.direction == "long" else -1
        stop = c.stop + offset
        tp1 = c.tp1 + offset
        tp2 = c.tp2 + offset
        obstacle = c.obstacle + offset if c.obstacle is not None else None
        if c.entry_type == "limit":
            entry = c.entry + offset
        else:
            entry = ask if c.direction == "long" else bid
        tp1 = rebase_tp1(c.direction, entry, stop, tp1, float(profile.get("targets", {}).get("tp1_rr", 1.0)),
                         self.cfg.min_rr)
        ok, rr, why = rr_check(c.direction, entry, stop, tp1, tp2, obstacle, self.cfg.min_rr)
        if not ok:
            return None, f"SKIPPED {why}"
        risk_money = equity * risk_pct / 100.0 * factor * c.size_factor * float(profile.get("risk_mult", 1.0))
        lots, note = lot_size(risk_money, entry, stop, spec)
        if lots <= 0:
            return None, f"SKIPPED size: {note}"
        actual = loss_at_stop(lots, entry, stop, spec)
        tg = profile["targets"]
        return {
            "candidate_id": c.id, "account": account_name, "symbol": c.symbol, "direction": c.direction,
            "side": "buy" if sign > 0 else "sell", "entry": entry, "entry_type": c.entry_type, "stop": stop,
            "tp1": tp1, "tp2": tp2, "obstacle": obstacle, "lots": lots, "risk_money": actual,
            "risk_budget": risk_money, "risk_pct": 100.0 * actual / equity if equity else None, "rr": rr,
            "offset": offset, "size_factor": factor * c.size_factor, "tp1_fraction": float(tg["tp1_fraction"]),
            "tp2_fraction": float(tg.get("tp2_fraction", 0.0)), "note": note,
        }, "ok"
