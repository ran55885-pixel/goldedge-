"""Market regime engine: trend / range / expansion / compression (+ confidence).

Inputs: HTF structure (trend + BOS frequency), ADR-used %, ATR percentile, Volume Profile shape.
Playbooks are enabled/weighted per regime by the Strategist profiles.
"""
from __future__ import annotations


def classify(structure_trend: str, bos_count: int, choch_count: int, atr_pct: float | None,
             adr_used: float | None, vp_balanced: bool | None) -> dict:
    scores = {"trend": 0.0, "range": 0.0, "expansion": 0.0, "compression": 0.0}
    reasons = []
    if structure_trend in ("bull", "bear"):
        scores["trend"] += 1.0
        reasons.append(f"structure {structure_trend}")
    if bos_count >= 2 and bos_count > choch_count:
        scores["trend"] += 1.0
        reasons.append(f"{bos_count} BOS")
    if choch_count >= 2 and choch_count >= bos_count:
        scores["range"] += 1.2
        reasons.append(f"{choch_count} CHoCH flips")
    if atr_pct is not None:
        if atr_pct >= 80:
            scores["expansion"] += 1.5
            reasons.append(f"ATR pct {atr_pct:.0f}")
        elif atr_pct <= 20:
            scores["compression"] += 1.5
            reasons.append(f"ATR pct {atr_pct:.0f}")
        else:
            scores["range"] += 0.3
    if adr_used is not None:
        if adr_used >= 90:
            scores["expansion"] += 0.7
        elif adr_used <= 40:
            scores["compression"] += 0.4
    if vp_balanced is True:
        scores["range"] += 0.8
        reasons.append("balanced profile")
    elif vp_balanced is False:
        scores["trend"] += 0.6
        reasons.append("trending profile")
    regime = max(scores, key=scores.get)
    total = sum(scores.values()) or 1.0
    return {"regime": regime, "confidence": round(scores[regime] / total, 2), "scores": scores,
            "reasons": reasons}
