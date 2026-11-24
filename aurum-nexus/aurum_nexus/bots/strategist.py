"""STRATEGIST - per-symbol strategy agent.

Each symbol has a versioned profile config/profiles/<SYMBOL>.yaml: enabled playbooks & weights, entry
timeframes, sessions, stop/target style, time-target params and regime preferences.
Learning sources: walk-forward optimisation (mt5sys optimize) and live/paper results via the Analyst
(gradual, after a minimum sample). Every change is logged with its reason and versioned.
Rules: profiles can be locked by the user; auto-apply is OFF in live (proposals wait for the Apply button)
and ON in paper; parameters are clamped to bounded ranges; a profile can NEVER relax min_rr, min_score
or any Guardian rule (those keys are not part of a profile and are rejected).
"""
from __future__ import annotations

import copy
import time
from pathlib import Path

import yaml

from aurum_nexus.bots.base import Bot
from aurum_nexus.symbols import ASIA_PAIRS, canonical

SETUPS = ["zone_reaction", "sweep_reversal", "bos_retest", "fvg_ob", "range_breakout", "orb", "vwap_reversion",
          "wyckoff", "london_judas", "ny_open_reversal", "second_leg", "asia_range_reversion"]
REVERSAL_SETUPS = {"sweep_reversal", "vwap_reversion", "wyckoff", "london_judas", "ny_open_reversal",
                   "asia_range_reversion", "zone_reaction"}

FORBIDDEN_KEYS = {"min_rr", "min_score", "guardian", "risk", "kill_switch", "max_open_risk_pct",
                  "daily_max_loss_pct", "refuse_live_on_fallback"}

BOUNDS = {
    "stops.atr_mult": (0.8, 4.0),
    "stops.min_spread_mult": (3.0, 15),
    "stops.atr_buffer_mult": (0.1, 0.8),
    "stops.min_atr": (0.2, 1.0),
    "stops.max_atr": (1.5, 5.0),
    "targets.tp1_rr": (1.0, 2.5),
    "targets.tp1_fraction": (0.25, 0.75),
    "targets.tp2_fraction": (0.0, 0.6),
    "targets.tp2_rr_default": (1.2, 4.0),
    "targets.liq_max_rr": (2.0, 10.0),
    "targets.liq_front_atr": (0.0, 0.5),
    "time_targets.reaction_bars": (2, 8),
    "time_targets.t1_mult": (1.0, 4.0),
    "time_targets.t2_mult": (1.0, 5.0),
    "orb_minutes": (15, 30),
    "risk_mult": (0.25, 1.0),
    "min_score_offset": (0, 20),
    "playbooks.*.weight": (0.5, 1.5),
}

ALL_SESS = ["london_open", "ny_open", "overlap", "london", "newyork"]


def _pb(enabled=True, weight=1.0, sessions=None, entry="market", timeframes=None):
    return {"enabled": enabled, "weight": weight, "sessions": sessions or list(ALL_SESS), "entry": entry,
            "timeframes": timeframes or ["M1", "M5", "M15"]}


def default_profile(symbol: str) -> dict:
    s = canonical(symbol)
    p = {
        "symbol": s, "version": 1, "locked": False, "updated": None,
        "notes": "starter defaults - adjusted by walk-forward optimisation and results (see README section 13)",
        "entry_timeframes": ["M1", "M5", "M15"],
        "sessions_allowed": ["london", "newyork", "overlap"],
        "asia_opt_in": False,
        "playbooks": {
            "zone_reaction": _pb(entry="limit"),
            "sweep_reversal": _pb(),
            "bos_retest": _pb(entry="limit"),
            "fvg_ob": _pb(entry="limit"),
            "range_breakout": _pb(weight=0.9),
            "orb": _pb(sessions=["london_open", "ny_open"], timeframes=["M1", "M5"]),
            "vwap_reversion": _pb(weight=0.9, sessions=["london", "newyork", "overlap"]),
            "wyckoff": _pb(weight=0.9),
            "london_judas": _pb(sessions=["london_open"], timeframes=["M1", "M5"]),
            "ny_open_reversal": _pb(sessions=["ny_open"], timeframes=["M1", "M5"]),
            "second_leg": _pb(timeframes=["M1", "M5"]),
            "asia_range_reversion": _pb(enabled=False, weight=0.8, sessions=["asia"], timeframes=["M5", "M15"]),
        },
        # ATR stop (default): stop = entry -/+ atr_mult x ATR(atr_tf), never closer than min_spread_mult x spread.
        # "structure" (anchor + ATR buffer) remains available per profile.
        "stops": {"style": "atr", "atr_tf": "M15", "atr_mult": 1.5, "min_spread_mult": 5.0,
                  "atr_buffer_mult": 0.25, "min_atr": 0.4, "max_atr": 3.0},
        # targets.mode: "rr" = TP1 at tp1_rr, TP2 at the next obstacle / tp2_rr_default;
        #               "liquidity" = TP1 at the next opposing liquidity pool, TP2 at the one after (liq_max_rr cap)
        #               "liquidity_runner" = TP1 at tp1_rr, TP2 (runner) at the next liquidity beyond TP1
        "targets": {"mode": "rr", "tp1_rr": 1.0, "tp1_fraction": 0.5, "tp2_fraction": 0.3, "tp2_rr_default": 2.0,
                    "trail": "m5_structure", "liq_front_atr": 0.1, "liq_max_rr": 6.0},
        "time_targets": {"reaction_bars": 3, "t1_mult": 2.0, "t2_mult": 2.0},
        "regime_preferences": {
            "trend": {"prefer": ["bos_retest", "second_leg", "fvg_ob", "orb"], "avoid": ["vwap_reversion", "wyckoff"]},
            "range": {"prefer": ["vwap_reversion", "wyckoff", "zone_reaction", "sweep_reversal"],
                      "avoid": ["range_breakout"]},
            "expansion": {"prefer": ["range_breakout", "orb", "second_leg"], "avoid": ["vwap_reversion"]},
            "compression": {"prefer": ["range_breakout", "sweep_reversal"], "avoid": []},
        },
        # no new entries during these local hours (e.g. [15, 16, 17, 18, 19] = 15:00-19:59 Israel time)
        "blocked_hours": {"tz": "Asia/Jerusalem", "hours": []},
        "orb_minutes": 15,
        "orb_stop": "mid",
        "risk_mult": 1.0,
        "min_score_offset": 0,
        "regime_overrides": {},
    }
    pb = p["playbooks"]
    if s in ("XAUUSD", "XAGUSD"):
        p["notes"] = "Gold: London/NY sweep_reversal + ORB focus, wider ATR stops, Asia blocked (opt-in Asia-range reversion)"
        pb["sweep_reversal"].update(weight=1.25, sessions=["london_open", "ny_open", "overlap"])
        pb["orb"].update(weight=1.2)
        p["stops"].update(atr_buffer_mult=0.35, min_atr=0.5, max_atr=3.5)
        p["targets"]["tp2_rr_default"] = 2.2
    elif s in ("EURUSD", "GBPUSD"):
        p["notes"] = f"{s}: London bos_retest focus, London/NY sessions"
        pb["bos_retest"].update(weight=1.25, sessions=["london_open", "london", "overlap"])
        if s == "GBPUSD":
            pb["london_judas"].update(weight=1.15)
    if s in ASIA_PAIRS:
        p["notes"] = f"{s}: Asia session allowed (Asia playbooks enabled)"
        p["sessions_allowed"] = ["asia", "london", "newyork", "overlap"]
        pb["asia_range_reversion"]["enabled"] = True
        for name in ("zone_reaction", "sweep_reversal", "bos_retest", "fvg_ob"):
            pb[name]["sessions"] = pb[name]["sessions"] + ["asia"]
    return p


def _get(d: dict, path: str):
    node = d
    for part in path.split("."):
        node = node[part]
    return node


def _set(d: dict, path: str, value) -> None:
    parts = path.split(".")
    node = d
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def clamp_profile(p: dict, min_rr: float = 1.0) -> tuple[dict, list[str]]:
    """Clamp a profile to its bounded ranges and strip forbidden keys. Returns (profile, warnings)."""
    p = copy.deepcopy(p)
    warns = []
    for k in list(p.keys()):
        if k in FORBIDDEN_KEYS:
            warns.append(f"profile key '{k}' is not allowed (safety floors are global) - removed")
            del p[k]
    for path, (lo, hi) in BOUNDS.items():
        if "*" in path:
            pre, post = path.split(".*.")
            for name, pb in (p.get(pre) or {}).items():
                v = pb.get(post)
                if v is not None and not (lo <= v <= hi):
                    warns.append(f"{pre}.{name}.{post}={v} clamped to [{lo},{hi}]")
                    pb[post] = max(lo, min(hi, v))
            continue
        try:
            v = _get(p, path)
        except (KeyError, TypeError):
            continue
        if v is None:
            continue
        if not (lo <= v <= hi):
            warns.append(f"{path}={v} clamped to [{lo},{hi}]")
            _set(p, path, type(lo)(max(lo, min(hi, v))) if isinstance(lo, int) else max(lo, min(hi, v)))
    tp1 = p.get("targets", {}).get("tp1_rr", 1.0)
    if tp1 < min_rr:
        warns.append(f"targets.tp1_rr={tp1} below min_rr {min_rr}; raised")
        p["targets"]["tp1_rr"] = min_rr
    if p.get("min_score_offset", 0) < 0:
        p["min_score_offset"] = 0
        warns.append("min_score_offset cannot be negative (cannot lower min_score)")
    return p, warns


def flatten_changes(changes: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in changes.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict) and not path.startswith("regime_preferences"):
            out.update(flatten_changes(v, path))
        else:
            out[path] = v
    return out


class Strategist(Bot):
    name = "STRATEGIST"
    color = "#b388ff"
    job = "Per-symbol strategy profiles, learning proposals & approvals"

    def __init__(self, bus, cfg, storage, profiles_dir: Path | None = None, clock=None):
        super().__init__(bus, cfg, clock)
        self.storage = storage
        self.dir = Path(profiles_dir) if profiles_dir else (cfg.config_dir / "profiles")
        self.profiles: dict[str, dict] = {}
        self.live_mode = False
        self.oos: dict[str, dict] = {}

    def config_view(self) -> dict:
        return {"auto_apply_paper": self.cfg.get("strategist.auto_apply_paper"),
                "auto_apply_live": self.cfg.get("strategist.auto_apply_live"), "live_mode": self.live_mode}

    def load(self, symbols: list[str]) -> None:
        for s in symbols:
            path = self.dir / f"{s}.yaml"
            prof = default_profile(s)
            if path.exists():
                with open(path, encoding="utf-8") as f:
                    loaded = yaml.safe_load(f) or {}
                prof = _merge(prof, loaded)
                if "atr_tf" not in (loaded.get("stops") or {}):
                    # profile written before the ATR stop model: migrate to the new default stop
                    prof["stops"] = copy.deepcopy(default_profile(s)["stops"])
                    self.log("STRATEGY", f"{s} profile: stop model migrated to ATR "
                             f"({prof['stops']['atr_mult']} x ATR {prof['stops']['atr_tf']}, "
                             f">= {prof['stops']['min_spread_mult']} x spread)", s)
            prof, warns = clamp_profile(prof, self.cfg.min_rr)
            for w in warns:
                self.log("STRATEGY", f"{s} profile: {w}", s)
            self.profiles[s] = prof
            self.oos[s] = (prof.get("oos_stats") or {})
        self.set_status(f"{len(self.profiles)} profiles loaded")

    def profile(self, symbol: str) -> dict:
        if symbol not in self.profiles:
            self.profiles[symbol] = clamp_profile(default_profile(symbol), self.cfg.min_rr)[0]
        return self.profiles[symbol]

    def save(self, symbol: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / f"{symbol}.yaml", "w", encoding="utf-8") as f:
            yaml.safe_dump(self.profiles[symbol], f, sort_keys=False, allow_unicode=True)

    @property
    def auto_apply(self) -> bool:
        if self.live_mode:
            return bool(self.cfg.get("strategist.auto_apply_live", False))
        return bool(self.cfg.get("strategist.auto_apply_paper", True))

    def set_lock(self, symbol: str, locked: bool) -> None:
        self.profile(symbol)["locked"] = bool(locked)
        self.save(symbol)
        self.log("STRATEGY", f"{symbol} profile {'LOCKED' if locked else 'unlocked'} by user", symbol)

    def propose(self, symbol: str, changes: dict, reason: str, source: str, report: dict | None = None) -> dict:
        flat = flatten_changes(changes)
        bad = [k for k in flat if k.split(".")[0] in FORBIDDEN_KEYS]
        if bad:
            self.log("STRATEGY", f"{symbol}: proposal rejected - touches safety keys {bad}", symbol)
            return {"status": "rejected", "reason": f"forbidden keys {bad}"}
        pid = self.storage.add_proposal(symbol, flat, reason, source, report)
        self.log("STRATEGY", f"{symbol}: proposal #{pid} from {source}: {reason}", symbol, {"changes": flat})
        prof = self.profile(symbol)
        if prof.get("locked"):
            return {"status": "pending", "id": pid, "reason": "profile locked"}
        if self.auto_apply:
            return self.apply(pid, by="auto (paper)")
        return {"status": "pending", "id": pid, "reason": "auto-apply off (live) - approve on dashboard"}

    def apply(self, proposal_id: int, by: str = "user") -> dict:
        rows = [r for r in self.storage.proposals(status=None) if r["id"] == int(proposal_id)]
        if not rows:
            return {"status": "error", "reason": "proposal not found"}
        prop = rows[0]
        if prop["status"] != "pending":
            return {"status": "error", "reason": f"proposal is {prop['status']}"}
        sym = prop["symbol"]
        prof = self.profile(sym)
        if prof.get("locked") and by != "user":
            return {"status": "error", "reason": "profile locked"}
        new = copy.deepcopy(prof)
        for path, v in prop["changes"].items():
            _set(new, path, v)
        new, warns = clamp_profile(new, self.cfg.min_rr)
        new["version"] = int(prof.get("version", 1)) + 1
        new["updated"] = time.time()
        self.profiles[sym] = new
        self.save(sym)
        self.storage.set_proposal_status(int(proposal_id), "applied")
        self.storage.save_profile_change(sym, new["version"], prop["changes"], prop["reason"], f"{prop['source']} / {by}")
        self.log("STRATEGY", f"{sym}: proposal #{proposal_id} APPLIED by {by} -> profile v{new['version']}"
                 + (f" (clamped: {'; '.join(warns)})" if warns else ""), sym)
        return {"status": "applied", "id": int(proposal_id), "version": new["version"], "warnings": warns}

    def reject(self, proposal_id: int) -> dict:
        self.storage.set_proposal_status(int(proposal_id), "rejected")
        self.log("STRATEGY", f"proposal #{proposal_id} rejected by user")
        return {"status": "rejected", "id": int(proposal_id)}

    def learn(self, symbol: str, setup_stats: dict) -> list[dict]:
        """Gradual weight nudges from Analyst stats (after the minimum sample). Bounded by BOUNDS."""
        out = []
        prof = self.profile(symbol)
        min_n = int(self.cfg.get("strategist.learn_min_trades", 15))
        changes = {}
        reasons = []
        for setup, st in setup_stats.items():
            if setup not in prof["playbooks"] or st.get("trades", 0) < min_n:
                continue
            w = float(prof["playbooks"][setup]["weight"])
            exp = float(st.get("expectancy_r", 0.0))
            if exp < -0.1 and w > 0.5:
                changes[f"playbooks.{setup}.weight"] = round(max(0.5, w - 0.1), 2)
                reasons.append(f"{setup} expectancy {exp:+.2f}R over {st['trades']} trades")
            elif exp > 0.25 and w < 1.5:
                changes[f"playbooks.{setup}.weight"] = round(min(1.5, w + 0.05), 2)
                reasons.append(f"{setup} expectancy {exp:+.2f}R over {st['trades']} trades")
        if changes:
            out.append(self.propose(symbol, changes, "; ".join(reasons), "analyst results"))
        return out

    def summary(self, symbol: str, regime: str | None = None) -> dict:
        p = self.profile(symbol)
        active = [k for k, v in p["playbooks"].items() if v.get("enabled")]
        pref = (p.get("regime_preferences") or {}).get(regime or "", {})
        return {"symbol": symbol, "version": p.get("version"), "locked": p.get("locked"),
                "active_playbooks": active, "weights": {k: p["playbooks"][k]["weight"] for k in active},
                "entry_timeframes": p.get("entry_timeframes"), "sessions": p.get("sessions_allowed"),
                "regime": regime, "regime_prefer": pref.get("prefer", []), "regime_avoid": pref.get("avoid", []),
                "stops": p.get("stops"), "targets": p.get("targets"), "time_targets": p.get("time_targets"),
                "oos": self.oos.get(symbol) or p.get("oos_stats") or {}, "notes": p.get("notes", ""),
                "proposals": self.storage.proposals("pending", symbol)}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "regime_preferences":
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out
