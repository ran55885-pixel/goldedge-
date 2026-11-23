"""Anti-rug checks: RugCheck for Solana, GoPlus for Base. Results are cached."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

RUGCHECK = "https://api.rugcheck.xyz/v1/tokens/{}/report/summary"
GOPLUS = "https://api.gopluslabs.io/api/v1/token_security/{}"
GOPLUS_CHAIN_IDS = {"base": "8453", "ethereum": "1", "bsc": "56"}

# GoPlus flags where "1" means the contract can hurt holders.
GOPLUS_FATAL = {
    "is_honeypot": "honeypot",
    "cannot_sell_all": "cannot sell all",
    "cannot_buy": "cannot buy",
    "is_mintable": "owner can mint",
    "hidden_owner": "hidden owner",
    "can_take_back_ownership": "can take back ownership",
    "owner_change_balance": "owner can change balances",
    "transfer_pausable": "transfers can be paused",
    "slippage_modifiable": "tax can be changed",
    "personal_slippage_modifiable": "per-wallet tax",
    "selfdestruct": "self-destruct",
    "honeypot_with_same_creator": "creator made honeypots before",
}


@dataclass
class SafetyResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    risk_score: float = 0.0  # 0 = clean, 100 = terrible
    raw: dict = field(default_factory=dict)


class Safety:
    def __init__(self, cfg: dict, ttl_seconds: int = 600):
        self.cfg = cfg
        self.ttl = ttl_seconds
        self.cache: dict[tuple[str, str], tuple[float, SafetyResult]] = {}
        self.http = httpx.Client(timeout=20.0, headers={"Accept": "application/json"})

    def check(self, chain: str, token: str) -> SafetyResult:
        key = (chain, token)
        hit = self.cache.get(key)
        if hit and time.time() - hit[0] < self.ttl:
            return hit[1]
        try:
            if chain == "solana":
                res = evaluate_rugcheck(self._fetch(RUGCHECK.format(token)), self.cfg)
            elif chain in GOPLUS_CHAIN_IDS:
                data = self._fetch(GOPLUS.format(GOPLUS_CHAIN_IDS[chain]),
                                   {"contract_addresses": token})
                info = (data.get("result") or {}).get(token.lower()) or {}
                res = evaluate_goplus(info, self.cfg)
            else:
                res = SafetyResult(False, [f"no safety source for {chain}"], 100)
        except (httpx.HTTPError, ValueError) as e:
            res = SafetyResult(not self.cfg.get("fail_if_unknown", True),
                               [f"safety API error: {type(e).__name__}"], 100)
        self.cache[key] = (time.time(), res)
        return res

    def _fetch(self, url: str, params: dict | None = None) -> dict:
        r = self.http.get(url, params=params)
        r.raise_for_status()
        return r.json()


def evaluate_rugcheck(data: dict, cfg: dict) -> SafetyResult:
    reasons = []
    if not data:
        return SafetyResult(not cfg.get("fail_if_unknown", True), ["no RugCheck data"], 100)
    score = float(data.get("score_normalised", 100))
    if score > cfg.get("solana_max_rugcheck_score", 40):
        reasons.append(f"RugCheck score {score:.0f}")
    if cfg.get("solana_block_danger_risks", True):
        for r in data.get("risks") or []:
            if str(r.get("level", "")).lower() == "danger":
                reasons.append(r.get("name", "danger risk"))
    return SafetyResult(not reasons, reasons, min(100.0, score), data)


def evaluate_goplus(info: dict, cfg: dict) -> SafetyResult:
    if not info:
        return SafetyResult(not cfg.get("fail_if_unknown", True), ["no GoPlus data yet"], 100)
    reasons = [label for k, label in GOPLUS_FATAL.items() if str(info.get(k, "0")) == "1"]
    if str(info.get("is_open_source", "0")) != "1":
        reasons.append("contract not verified")
    max_tax = cfg.get("base_max_tax_pct", 10) / 100
    for k in ("buy_tax", "sell_tax"):
        try:
            tax = float(info.get(k) or 0)
        except ValueError:
            tax = 1.0
        if tax > max_tax:
            reasons.append(f"{k} {tax * 100:.0f}%")
    risk = min(100.0, 25.0 * len(reasons))
    return SafetyResult(not reasons, reasons, risk, info)
