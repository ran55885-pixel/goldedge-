"""Configuration loading, validation and hard safety floors.

Hard floors (cannot be relaxed by config, optimizer, Strategist or any agent):
  * risk.min_rr   >= 1.0   (minimum risk:reward 1:1)
  * risk.min_score >= 70   (minimum signal score)
  * Guardian veto is always active (cannot be disabled).
Values below a floor are clamped and a warning is recorded (also reported by `mt5sys check`).

Secrets are never stored in YAML: accounts reference an environment variable name
(`password_env`) and the value is read from `.env` / the environment at runtime.
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

MIN_RR_FLOOR = 1.0
MIN_SCORE_FLOOR = 70.0

import sys as _sys

# Frozen (PyInstaller) build: config/, data/ and .env live next to "Aurum Nexus.exe"
PROJECT_ROOT = Path(_sys.executable).resolve().parent if getattr(_sys, "frozen", False) \
    else Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"

DEFAULT_SYMBOLS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY"]
OPTIONAL_SYMBOLS = ["XAGUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "EURJPY", "GBPJPY", "AUDJPY"]

DEFAULTS: dict[str, Any] = {
    "system": {
        "name": "Aurum Nexus",
        "mode": "paper",                 # paper | live
        "price_source": "auto",          # auto | mt5 | oanda | fallback
        "analysis_account": "auto",      # account whose MT5 terminal is the analysis feed (auto = first connected)
        "server_utc_offset_hours": 2,    # default MT5 server offset (per account override)
        "day_boundary": {"tz": "America/New_York", "time": "17:00"},  # broker trading day start
        "cycle_seconds": 1.0,
        "data_dir": "data",
        "stale_after_seconds": 6,
        "paper_on_fallback": True,       # paper simulation allowed on preview data (labelled); live never
        "paper_starting_balance": 10000.0,
        "paper_currency": "USD",
    },
    "symbols": {
        "enabled": list(DEFAULT_SYMBOLS),
        "optional": list(OPTIONAL_SYMBOLS),
        "suffix_candidates": ["", ".m", "m", ".r", ".pro", ".ecn", "-ecn", ".a", "_i", ".i", "+", ".std", ".raw", "#"],
        "levels_round_steps": {"adr_fractions": [0.25, 0.5, 1.0], "price_fractions_fallback": [0.0025, 0.005, 0.01]},
        "usd_exposure_max_positions": 3,
        "same_currency_max_positions": 2,
        "correlation_block_threshold": 0.75,
    },
    "scanner": {
        "htf": ["D1", "H4", "H1"],
        "ltf": ["M15", "M5", "M1"],      # LTF structure/zones/FVG/liquidity scanned too -> more opportunities
        "swing_lookback": {"D1": 2, "H4": 2, "H1": 3, "M15": 3, "M5": 3, "M1": 3},
        "zone": {"impulse_atr_mult": 1.5, "impulse_body_ratio": 0.55, "base_max_bars": 3,
                 "base_max_range_atr": 0.9, "max_touches": 2, "max_zones": 12},
        "pivot_cluster_atr_mult": 0.35,
        "equal_level_atr_mult": 0.12,
        "vp_rows": 80,
        "value_area_pct": 0.70,
        "atr_period": 14,
        "adr_days": 14,
        "bars": {"D1": 120, "H4": 300, "H1": 500, "M15": 700, "M5": 900, "M1": 1500},
    },
    "levels": {
        "cluster_atr_mult": 0.35,
        "top_n": 6,
        "watch_atr_mult": 1.0,
        "ready_rvol": 1.5,
        "ready_wick_ratio": 0.5,
        "ready_valid_bars": 6,
        "broken_closes": 2,
        "broken_rvol": 1.2,
        "grade_thresholds": {"A+": 80, "A": 65, "B": 50},
    },
    "dxy": {
        "broker_symbols": ["DXY", "USDX", "DX.f", "USDIDX", "DXY.cash", "USDINDEX", "DX"],
        "corr_bars": 192,
        "decoupled_abs_corr": 0.3,
        "max_modifier": 10,
        "block_on_dxy_breakout": True,
        "shock_atr_mult": 2.0,
    },
    "signal": {
        "entry_timeframes": ["M1", "M5", "M15"],
        "adr_exhaustion_pct": 85,
        "weights": {"zone_quality": 15, "confluence": 15, "confirmation": 15, "bias": 15,
                    "volume_flow": 20, "proximity": 5, "stop_quality": 10, "conditions": 5},
        "modifier_caps": {"levels_ready": 8, "dxy": 10, "regime": 5, "sentiment_macro": 5,
                          "seasonality": 5, "analyst": 15, "total": 25},
        "delta_method": "clv",           # clv | m1_updown
        "rvol_days": 10,
        "orb_minutes": [15, 30],
        "asia_gold_opt_in": False,
    },
    "risk": {
        "min_rr": 1.0,
        "min_score": 70,
        "default_risk_pct": 0.5,
        "atr_buffer_mult": 0.25,
        "tp1_rr": 1.0,
        "tp1_fraction": 0.5,
        "move_be_after_tp1": True,
        "volatility_scaling": False,
        "loss_streak_reduction": {"after_losses": 2, "factor": 0.5},
        "drawdown_reduction": [{"dd_pct": 5.0, "factor": 0.75}, {"dd_pct": 10.0, "factor": 0.5}],
        "equity_curve_filter": {"enabled": True, "lookback_trades": 10, "factor": 0.75},
        "min_size_factor": 0.25,
        "daily_profit_lock": {"enabled": True, "lock_at_r": 3.0, "giveback_pct": 50.0, "min_peak_r": 1.0},
        "max_stop_atr_mult": 4.0,
        "min_stop_atr_mult": 0.3,
    },
    "guardian": {
        "daily_max_loss_pct": 2.0,
        "max_trades_per_day": 12,
        "max_trades_per_symbol_per_day": 4,
        "max_concurrent_positions": 4,
        "max_open_risk_pct": 4.0,
        "min_size_fraction_under_cap": 0.5,
        "max_spread_atr_frac": 0.25,
        "spread_spike_mult": 2.5,
        "consecutive_losses_lockout": 4,
        "revenge_block_minutes": 30,
        "symbol_cooldown_minutes": 5,
        "max_trades_per_hour": 4,
        "monday_open_block_minutes": 60,
        "friday_close_block_hour_utc": 19,
        "kill_switch": False,
        "refuse_live_on_fallback": True,  # cannot be disabled (enforced in code)
    },
    "sessions": {
        "asia": {"tz": "Asia/Tokyo", "start": "08:00", "end": "15:00"},
        "london": {"tz": "Europe/London", "start": "07:00", "end": "16:00"},
        "newyork": {"tz": "America/New_York", "start": "08:00", "end": "17:00"},
        "london_open": {"tz": "Europe/London", "start": "07:00", "end": "10:00"},
        "ny_open": {"tz": "America/New_York", "start": "08:20", "end": "11:00"},
        "overlap": {"tz": "America/New_York", "start": "08:00", "end": "11:30"},
        "asia_range": {"tz": "Asia/Tokyo", "start": "09:00", "end": "15:00"},
        "orb_london": {"tz": "Europe/London", "start": "08:00"},
        "orb_ny": {"tz": "America/New_York", "start": "09:30"},
        "rollover": {"tz": "America/New_York", "start": "16:55", "end": "17:10"},
    },
    "trade_manager": {
        "rules": {
            "friday_close": {"enabled": True, "priority": 1, "hour_utc": 20, "minute": 30},
            "news_ahead": {"enabled": True, "priority": 2, "minutes": 10, "min_r_to_hold": 1.0},
            "end_of_day": {"enabled": True, "priority": 3, "minutes_before_rollover": 20},
            "end_of_session": {"enabled": False, "priority": 4},
            "ltf_choch": {"enabled": True, "priority": 5, "timeframe": "M5", "partial": 1.0},
            "htf_shift": {"enabled": True, "priority": 6, "timeframe": "H1"},
            "opposing_rejection": {"enabled": True, "priority": 7, "min_grade": "A", "partial": 0.5},
            "momentum_fade": {"enabled": True, "priority": 8, "bars": 6, "partial": 0.5},
            "cvd_divergence": {"enabled": True, "priority": 9, "partial": 0.5},
            "vwap_band_rejection": {"enabled": True, "priority": 10, "partial": 0.5},
            "dxy_shock": {"enabled": True, "priority": 11},
            "giveback": {"enabled": True, "priority": 12, "min_mfe_r": 1.0, "giveback_pct": 60.0},
            "spread_spike": {"enabled": True, "priority": 13, "mult": 3.0},
            "volatility_spike": {"enabled": True, "priority": 14, "atr_mult": 3.0},
        },
        "trail": {"timeframe": "M5", "buffer_atr_mult": 0.2},
    },
    "time_targets": {
        "enabled": True,
        "reaction_bars": 3,              # M5 bars to see a valid reaction after entering a zone
        "reaction_timeframe": "M5",
        "reaction_min_atr_move": 0.5,
        "t1_r": 0.5,
        "t1_mult": 2.0,
        "t2_mult": 2.0,
        "min_minutes": 5,
        "min_minutes_by_tf": {"M1": 10, "M5": 20, "M15": 45},
        "max_minutes": 240,
        "on_t1_miss": "tighten",         # tighten | partial | none
        "t1_tighten_to_r": -0.5,
        "t1_partial": 0.5,
        "on_t2_miss": "close",           # close | none
        "session_pace": {"asia": 0.6, "london": 1.0, "newyork": 1.0, "overlap": 1.25, "off": 0.5},
    },
    "execution": {
        "deviation_points": 20,
        "max_retries": 3,
        "magic": 770077,
        "avoid_first_seconds": 5,
        "limit_validity_bars": 6,
        "filling_preference": ["IOC", "FOK", "RETURN"],
        "slippage_points_backtest": 2,
    },
    "news": {
        "enabled": True,
        "url": "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        "refresh_minutes": 60,
        "minutes_before": 30,
        "minutes_after": 30,
        "impacts": ["High"],
        "manual_blackouts": [],          # [{"start": "2026-11-20T13:25:00Z", "end": "...", "currencies": ["USD"]}]
        "on_unavailable": "warn",        # warn (manual blackouts only) | block
    },
    "intel": {
        "regime": {"enabled": True},
        "dom": {"enabled": True, "levels_atr_mult": 0.5},
        "sentiment": {"enabled": True, "refresh_minutes": 30},
        "cot": {"enabled": True, "refresh_hours": 24},
        "macro": {"enabled": True, "refresh_hours": 12},
    },
    "analyst": {
        "min_sample": 15,
        "max_weight_adjust": 15,
        "disable_min_trades": 20,
        "disable_expectancy_r": -0.15,
        "probation_hours": 48,
        "seasonality_shrinkage": 20,
        "seasonality_cap": 5,
        "target_trades_per_day": 3,
        "frequency_warn_days": 5,
    },
    "strategist": {"auto_apply_paper": True, "auto_apply_live": False, "learn_min_trades": 15},
    "dashboard": {
        "host": "0.0.0.0",
        "port": 8787,
        "session_hours": 12,
        "login_max_attempts": 5,
        "login_window_seconds": 300,
        "refresh_seconds": 1.0,
    },
    "backtest": {
        "commission_per_lot": 7.0,
        "default_spread_points": {"XAUUSD": 25, "default": 12},
        "is_split": 0.7,
        "walk_forward": {"train_days": 20, "test_days": 5},
    },
}

LIVE_CONFIRM_PHRASE = "I UNDERSTAND"


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


@dataclass
class AccountConfig:
    name: str
    kind: str = "mt5"                 # mt5 | paper_only
    login: int | None = None
    server: str = ""
    terminal_path: str = ""
    password_env: str = ""
    mode: str = "paper"               # paper | live
    allow_live: bool = False
    allow_real: bool = False
    risk_pct: float = 0.5
    symbols: list[str] = field(default_factory=list)
    symbol_map: dict[str, str] = field(default_factory=dict)
    limits: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    server_utc_offset_hours: float | None = None
    magic: int | None = None
    starting_balance: float = 10000.0
    currency: str = "USD"

    def password(self) -> str | None:
        """Read the password from the environment at runtime; never logged."""
        if not self.password_env:
            return None
        val = os.environ.get(self.password_env)
        return val or None

    def public_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in ("password_env",)}
        d["has_password"] = bool(self.password())
        return d


@dataclass
class Config:
    data: dict
    accounts: list[AccountConfig]
    warnings: list[str] = field(default_factory=list)
    config_dir: Path = DEFAULT_CONFIG_DIR
    live_confirmed: bool = False

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def mode(self) -> str:
        return self.data["system"]["mode"]

    @property
    def min_rr(self) -> float:
        return float(self.data["risk"]["min_rr"])

    @property
    def min_score(self) -> float:
        return float(self.data["risk"]["min_score"])

    @property
    def symbols(self) -> list[str]:
        return list(self.data["symbols"]["enabled"])

    @property
    def data_dir(self) -> Path:
        p = Path(self.data["system"]["data_dir"])
        if not p.is_absolute():
            p = self.config_dir.parent / p
        return p

    def account(self, name: str) -> AccountConfig | None:
        for a in self.accounts:
            if a.name == name:
                return a
        return None


def enforce_floors(data: dict, warnings: list[str]) -> dict:
    """Clamp safety-critical values to their hard floors. Returns the same dict (mutated)."""
    risk = data.setdefault("risk", {})
    try:
        rr = float(risk.get("min_rr", MIN_RR_FLOOR))
    except (TypeError, ValueError):
        rr = MIN_RR_FLOOR
    if rr < MIN_RR_FLOOR:
        warnings.append(f"risk.min_rr={rr} is below the hard floor {MIN_RR_FLOOR}; clamped to {MIN_RR_FLOOR}")
        rr = MIN_RR_FLOOR
    risk["min_rr"] = rr
    try:
        ms = float(risk.get("min_score", MIN_SCORE_FLOOR))
    except (TypeError, ValueError):
        ms = MIN_SCORE_FLOOR
    if ms < MIN_SCORE_FLOOR:
        warnings.append(f"risk.min_score={ms} is below the hard floor {MIN_SCORE_FLOOR}; clamped to {MIN_SCORE_FLOOR}")
        ms = MIN_SCORE_FLOOR
    if ms > 100:
        warnings.append(f"risk.min_score={ms} > 100; clamped to 100")
        ms = 100.0
    risk["min_score"] = ms
    tp1 = float(risk.get("tp1_rr", 1.0))
    if tp1 < rr:
        warnings.append(f"risk.tp1_rr={tp1} below min_rr {rr}; raised to {rr}")
        risk["tp1_rr"] = rr
    g = data.setdefault("guardian", {})
    if not g.get("refuse_live_on_fallback", True):
        warnings.append("guardian.refuse_live_on_fallback cannot be disabled; forced to true")
    g["refuse_live_on_fallback"] = True
    mode = data.get("system", {}).get("mode", "paper")
    if mode not in ("paper", "live"):
        warnings.append(f"system.mode={mode!r} invalid; using 'paper'")
        data["system"]["mode"] = "paper"
    src = data.get("system", {}).get("price_source", "auto")
    if src not in ("auto", "mt5", "oanda", "fallback"):
        warnings.append(f"system.price_source={src!r} invalid; using 'auto'")
        data["system"]["price_source"] = "auto"
    # The total open-risk cap is a safety value: keep within (0, 10]
    cap = float(g.get("max_open_risk_pct", 4.0))
    if cap <= 0 or cap > 10:
        warnings.append(f"guardian.max_open_risk_pct={cap} out of range; set to 4.0")
        g["max_open_risk_pct"] = 4.0
    return data


def _parse_accounts(raw: dict | None, warnings: list[str]) -> list[AccountConfig]:
    out: list[AccountConfig] = []
    if not raw:
        return out
    names: set[str] = set()
    for item in raw.get("accounts", []) or []:
        if not isinstance(item, dict) or not item.get("name"):
            warnings.append("account entry without a name ignored")
            continue
        if "password" in item:
            warnings.append(f"account {item['name']}: plain 'password' in YAML is ignored - use password_env + .env")
            item = {k: v for k, v in item.items() if k != "password"}
        known = set(AccountConfig.__dataclass_fields__)
        unknown = set(item) - known
        if unknown:
            warnings.append(f"account {item['name']}: unknown keys ignored: {sorted(unknown)}")
        acc = AccountConfig(**{k: v for k, v in item.items() if k in known})
        if acc.name in names:
            warnings.append(f"duplicate account name {acc.name} ignored")
            continue
        names.add(acc.name)
        if acc.kind not in ("mt5", "paper_only"):
            warnings.append(f"account {acc.name}: kind={acc.kind!r} invalid; using mt5")
            acc.kind = "mt5"
        if acc.mode not in ("paper", "live"):
            warnings.append(f"account {acc.name}: mode={acc.mode!r} invalid; using paper")
            acc.mode = "paper"
        if acc.kind == "paper_only" and acc.mode == "live":
            warnings.append(f"account {acc.name}: paper_only account cannot be live")
            acc.mode = "paper"
        if not (0 < float(acc.risk_pct) <= 3.0):
            warnings.append(f"account {acc.name}: risk_pct={acc.risk_pct} out of (0,3]; set to 0.5")
            acc.risk_pct = 0.5
        out.append(acc)
    return out


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_env(config_dir: Path) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return
    for cand in (config_dir.parent / ".env", Path.cwd() / ".env"):
        if cand.exists():
            load_dotenv(cand, override=False)


def load_config(config_dir: str | Path | None = None, system_file: str | None = None,
                accounts_file: str | None = None, overrides: dict | None = None) -> Config:
    cdir = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
    load_env(cdir)
    warnings: list[str] = []
    sys_path = Path(system_file) if system_file else cdir / "system.yaml"
    data = deep_merge(DEFAULTS, load_yaml(sys_path))
    if overrides:
        data = deep_merge(data, overrides)
    enforce_floors(data, warnings)
    acc_path = Path(accounts_file) if accounts_file else cdir / "accounts.yaml"
    if not acc_path.exists():
        demo = cdir / "accounts.demo.yaml"
        if demo.exists():
            warnings.append(f"{acc_path.name} not found - using {demo.name} (paper demo)")
            acc_path = demo
    accounts = _parse_accounts(load_yaml(acc_path), warnings)
    return Config(data=data, accounts=accounts, warnings=warnings, config_dir=cdir)


def config_from_dict(data: dict | None = None, accounts: list[dict] | None = None) -> Config:
    """Build a Config from python dicts (tests / tools)."""
    warnings: list[str] = []
    merged = deep_merge(DEFAULTS, data or {})
    enforce_floors(merged, warnings)
    accs = _parse_accounts({"accounts": accounts or []}, warnings)
    return Config(data=merged, accounts=accs, warnings=warnings)


def effective_live(cfg: Config, acc: AccountConfig, trade_mode: str | None, source_is_preview: bool) -> tuple[bool, str]:
    """Return (is_live, reason). Live requires ALL gates; otherwise paper.

    trade_mode: "DEMO" | "CONTEST" | "REAL" | None (unknown -> treated as REAL for safety)
    """
    if cfg.mode != "live":
        return False, "system.mode=paper"
    if acc.kind != "mt5":
        return False, "paper_only account"
    if acc.mode != "live":
        return False, "account mode=paper"
    if not acc.allow_live:
        return False, "allow_live=false"
    tm = (trade_mode or "REAL").upper()
    if tm == "REAL" and not acc.allow_real:
        return False, "real account requires allow_real=true"
    if not cfg.live_confirmed:
        return False, f"live not confirmed (type '{LIVE_CONFIRM_PHRASE}' or --yes-live)"
    if source_is_preview:
        return False, "refusing live trading on fallback/preview data"
    return True, "live"
