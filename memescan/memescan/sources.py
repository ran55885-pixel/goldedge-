"""Market data: discover new/trending pools (GeckoTerminal) and get live pair stats (DexScreener)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

GT = "https://api.geckoterminal.com/api/v2"
DS = "https://api.dexscreener.com"
HEADERS = {"Accept": "application/json", "User-Agent": "memescan/0.1"}

# Quote tokens we never want to "buy" as if they were memecoins.
QUOTE_TOKENS = {
    "So11111111111111111111111111111111111111112",  # wSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC (sol)
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT (sol)
    "0x4200000000000000000000000000000000000006",  # WETH (base)
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC (base)
}


@dataclass
class Pair:
    chain: str
    token: str
    symbol: str
    name: str
    pair_address: str
    dex: str
    url: str
    price_usd: float
    liquidity_usd: float
    fdv_usd: float
    age_minutes: float
    volume: dict = field(default_factory=dict)        # m5,h1,h6,h24
    price_change: dict = field(default_factory=dict)  # m5,h1,h6,h24 (%)
    txns: dict = field(default_factory=dict)          # {"h1": {"buys":..,"sells":..}}

    def buys(self, w: str) -> int:
        return int(self.txns.get(w, {}).get("buys", 0))

    def sells(self, w: str) -> int:
        return int(self.txns.get(w, {}).get("sells", 0))


class Client:
    def __init__(self, timeout: float = 15.0):
        self.http = httpx.Client(timeout=timeout, headers=HEADERS)

    def _get(self, url: str, params: dict | None = None, retries: int = 3):
        for attempt in range(retries):
            try:
                r = self.http.get(url, params=params)
                if r.status_code == 429:
                    time.sleep(2 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()
            except (httpx.HTTPError, ValueError):
                if attempt == retries - 1:
                    raise
                time.sleep(1 + attempt)
        return None

    # ---------- discovery ----------
    def discover(self, chain: str, pages: int = 2) -> set[str]:
        """Token addresses from GeckoTerminal new + trending pools, plus DexScreener boosted/profiles."""
        tokens: set[str] = set()
        for kind in ("new_pools", "trending_pools"):
            for page in range(1, pages + 1):
                try:
                    data = self._get(f"{GT}/networks/{chain}/{kind}", {"page": page}) or {}
                except httpx.HTTPError:
                    continue
                for pool in data.get("data", []):
                    tid = pool["relationships"]["base_token"]["data"]["id"]
                    tokens.add(tid.split("_", 1)[1])
        for path in ("/token-profiles/latest/v1", "/token-boosts/latest/v1"):
            try:
                for item in self._get(DS + path) or []:
                    if item.get("chainId") == chain:
                        tokens.add(item["tokenAddress"])
            except httpx.HTTPError:
                pass
        return {t for t in tokens if t not in QUOTE_TOKENS and t.lower() not in QUOTE_TOKENS}

    # ---------- live stats ----------
    def pairs(self, chain: str, tokens: list[str]) -> dict[str, Pair]:
        """Best (most liquid) pair per token, from DexScreener, 30 tokens per call."""
        best: dict[str, Pair] = {}
        now_ms = time.time() * 1000
        for i in range(0, len(tokens), 30):
            batch = ",".join(tokens[i:i + 30])
            try:
                rows = self._get(f"{DS}/tokens/v1/{chain}/{batch}") or []
            except httpx.HTTPError:
                continue
            for p in rows:
                base = p.get("baseToken", {})
                addr = base.get("address", "")
                liq = float((p.get("liquidity") or {}).get("usd") or 0)
                if not addr or addr in QUOTE_TOKENS or addr.lower() in QUOTE_TOKENS:
                    continue
                if addr in best and best[addr].liquidity_usd >= liq:
                    continue
                created = p.get("pairCreatedAt") or now_ms
                best[addr] = Pair(
                    chain=chain,
                    token=addr,
                    symbol=base.get("symbol", "?"),
                    name=base.get("name", "?"),
                    pair_address=p.get("pairAddress", ""),
                    dex=p.get("dexId", ""),
                    url=p.get("url", ""),
                    price_usd=float(p.get("priceUsd") or 0),
                    liquidity_usd=liq,
                    fdv_usd=float(p.get("fdv") or p.get("marketCap") or 0),
                    age_minutes=max(0.0, (now_ms - created) / 60000),
                    volume={k: float(v or 0) for k, v in (p.get("volume") or {}).items()},
                    price_change={k: float(v or 0) for k, v in (p.get("priceChange") or {}).items()},
                    txns=p.get("txns") or {},
                )
        return best
