"""Hard filters + a 0-100 momentum score for a pair."""
from __future__ import annotations

from .sources import Pair


def hard_filter(p: Pair, f: dict) -> list[str]:
    """Return the list of reasons the pair fails. Empty list = passes."""
    fails = []
    if p.liquidity_usd < f["min_liquidity_usd"]:
        fails.append("low liquidity")
    if not f["min_fdv_usd"] <= p.fdv_usd <= f["max_fdv_usd"]:
        fails.append("fdv out of range")
    if p.age_minutes < f["min_age_minutes"]:
        fails.append("too new")
    if p.age_minutes > f["max_age_hours"] * 60:
        fails.append("too old")
    if p.volume.get("h1", 0) < f["min_volume_h1_usd"]:
        fails.append("low volume")
    tx = p.buys("h1") + p.sells("h1")
    if tx < f["min_txns_h1"]:
        fails.append("few txns")
    elif p.buys("h1") / tx < f["min_buy_ratio_h1"]:
        fails.append("sellers dominate")
    if p.price_change.get("h1", 0) < f.get("min_price_change_h1", -100):
        fails.append("no upward momentum")
    if p.price_change.get("m5", 0) < f.get("min_price_change_m5", -100):
        fails.append("dumping now")
    if p.price_change.get("h1", 0) > f["max_price_change_h1"]:
        fails.append("already parabolic")
    return fails


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def score(p: Pair) -> tuple[float, dict]:
    """0-100. Components are each 0..1, weighted. Returns (score, breakdown)."""
    h1 = p.price_change.get("h1", 0)
    m5 = p.price_change.get("m5", 0)
    tx_h1 = p.buys("h1") + p.sells("h1")
    tx_m5 = p.buys("m5") + p.sells("m5")

    parts = {
        # Price is moving up, and still moving up right now (not just a spike an hour ago).
        "momentum": _clamp(h1 / 150) * 0.6 + _clamp((m5 + 5) / 25) * 0.4,
        # Volume turning over the pool fast = real demand.
        "turnover": _clamp(p.volume.get("h1", 0) / max(p.liquidity_usd, 1) / 3),
        # More buys than sells, both in the last hour and the last 5 minutes.
        "buy_pressure": _clamp((p.buys("h1") / max(tx_h1, 1) - 0.5) / 0.15) * 0.5
        + _clamp((p.buys("m5") / max(tx_m5, 1) - 0.5) / 0.2) * 0.5,
        # Activity: number of trades in the last hour.
        "activity": _clamp(tx_h1 / 1500),
        # Acceleration: last 5 min volume vs the hourly average pace.
        "acceleration": _clamp((p.volume.get("m5", 0) * 12) / max(p.volume.get("h1", 1), 1) / 2),
        # Earlier is better: under 3h old scores best, fading to 0 at 48h.
        "early": _clamp(1 - (p.age_minutes - 180) / (48 * 60 - 180)),
        # Small cap = more room to multiply.
        "small_cap": _clamp(1 - (p.fdv_usd - 100_000) / 2_900_000),
    }
    weights = {"momentum": 25, "turnover": 15, "buy_pressure": 20, "activity": 10,
               "acceleration": 15, "early": 5, "small_cap": 10}
    total = sum(parts[k] * w for k, w in weights.items())
    return round(total, 1), {k: round(v, 2) for k, v in parts.items()}
