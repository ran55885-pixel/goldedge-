"""Intel engine: polls every source on its own interval, extracts token calls, alerts, and scores sources."""
from __future__ import annotations

import os
import sys
import time

import httpx

from ..notify import Notifier
from ..paper import DATA_DIR
from .extract import cashtags, evm_addresses, solana_addresses, topics
from .sources import EXCHANGES, Fetcher, Item
from .store import Store

FRESH_SECONDS = 30 * 60      # only items this recent count as a "call" (price at mention must be real)
IMPORTANT_TOPICS = {"listing", "hack"}


class Intel:
    def __init__(self, cfg: dict, store: Store | None = None, fetcher: Fetcher | None = None,
                 notifier: Notifier | None = None):
        self.cfg = cfg["intel"]
        self.store = store or Store(os.path.join(DATA_DIR, "intel.db"))
        self.fetch = fetcher or Fetcher(self.cfg["sources"])
        self.note = notifier or Notifier(cfg.get("telegram", {}).get("enabled", False))
        self.http = httpx.Client(timeout=15, headers={"Accept": "application/json"})
        self.last_run: dict[str, float] = {}
        self.first_pass: set[str] = set()

    # ---------------------------------------------------------- output
    def alert(self, text: str, kind: str, source: str, url: str = "", important: bool = True):
        self.store.log_event(kind, source, text, url, important)
        print(text + (f"\n  {url}" if url else ""), flush=True)
        if important:
            self.note.send(text + (f"\n{url}" if url else ""))

    # ---------------------------------------------------------- token resolution
    def resolve(self, addresses: list[str]) -> dict[str, dict]:
        """address -> {chain, symbol, price, url} using DexScreener (most liquid pair wins)."""
        out: dict[str, dict] = {}
        for i in range(0, len(addresses), 30):
            try:
                r = self.http.get("https://api.dexscreener.com/latest/dex/tokens/" + ",".join(addresses[i:i + 30]))
                pairs = r.json().get("pairs") or []
            except (httpx.HTTPError, ValueError):
                continue
            for p in pairs:
                addr = p["baseToken"]["address"]
                key = addr.lower() if addr.startswith("0x") else addr
                liq = float((p.get("liquidity") or {}).get("usd") or 0)
                if key in out and out[key]["liq"] >= liq:
                    continue
                out[key] = {"chain": p["chainId"], "symbol": p["baseToken"].get("symbol", "?"),
                            "price": float(p.get("priceUsd") or 0), "url": p.get("url", ""), "liq": liq}
        return out

    # ---------------------------------------------------------- item processing
    def process(self, items: list[Item], source_name: str, now: float | None = None):
        now = time.time() if now is None else now
        warmup = source_name not in self.first_pass  # first pass: remember, don't alert on old stuff
        self.first_pass.add(source_name)
        fresh_calls: dict[str, list[Item]] = {}
        for it in items:
            if not self.store.is_new(it.key):
                continue
            text = f"{it.title}\n{it.text}"
            if it.tier in ("news", "exchange", "aggregator") and not warmup:
                tags = topics(text)
                important = bool(tags & IMPORTANT_TOPICS)
                label = "/".join(sorted(tags)) or "news"
                tickers = " ".join(f"${t}" for t in sorted(cashtags(text) | set(it.symbols)) if t)[:60]
                self.alert(f"[{label.upper()}] {it.source}: {it.title}" + (f"  {tickers}" if tickers else ""),
                           label, it.source, it.url, important)
            is_fresh = (now - it.ts) < FRESH_SECONDS and not warmup
            if is_fresh:
                for addr in solana_addresses(text) | evm_addresses(text):
                    fresh_calls.setdefault(addr, []).append(it)
        if not fresh_calls:
            return
        resolved = self.resolve(sorted(fresh_calls))
        window = self.cfg["buzz_window_minutes"]
        for addr, info in resolved.items():
            for it in fresh_calls.get(addr, []):
                if not self.store.add_mention(it.source, it.group, info["chain"], addr, info["symbol"],
                                              info["price"], it.url, it.ts):
                    continue
                sources = self.store.buzz(addr, window, now)
                n = len({g for _, g in sources})
                if n >= self.cfg["min_independent_sources"]:
                    names = ", ".join(sorted({s for s, _ in sources}))
                    self.alert(f"[MULTI-SOURCE x{n}] {info['symbol']} ({info['chain']}) called by: {names}",
                               "buzz", it.source, info["url"], important=True)

    # ---------------------------------------------------------- exchange listings
    def check_listings(self):
        for exchange, symbols in self.fetch.exchange_markets().items():
            base = self.store.baseline(exchange)
            if not base:  # first run: learn what's already listed, alert on nothing
                self.store.add_baseline(exchange, symbols)
                print(f"[listings] {exchange}: baseline {len(symbols)} markets", file=sys.stderr)
                continue
            new = symbols - base
            if new and len(new) < 25:  # a huge diff = API glitch, not 25 real listings
                for sym in sorted(new):
                    link = EXCHANGES[exchange][2].format(sym)
                    self.alert(f"[NEW LISTING] {sym} is now trading on {exchange} "
                               f"(dex chart: https://dexscreener.com/search?q={sym})", "listing", exchange, link)
            self.store.add_baseline(exchange, new)

    # ---------------------------------------------------------- outcomes (reliability learning)
    def update_outcomes(self, now: float | None = None):
        now = time.time() if now is None else now
        pending = self.store.pending_outcomes(now)
        if not pending:
            return
        prices = self.resolve(sorted({row[2] for row in pending}))
        for mid, _chain, token, ts, p1, p24 in pending:
            price = (prices.get(token) or {}).get("price", 0.0)  # token gone from DexScreener = dead = 0
            if p1 is None and now - ts >= 3600:
                self.store.set_outcome(mid, "price_1h", price)
            if p24 is None and now - ts >= 86400:
                self.store.set_outcome(mid, "price_24h", price)

    # ---------------------------------------------------------- scheduler
    def due(self, name: str, now: float) -> bool:
        interval = (self.cfg["sources"].get(name) or {}).get("interval", 300)
        if not (self.cfg["sources"].get(name) or {}).get("enabled", True):
            return False
        if now - self.last_run.get(name, 0) >= interval:
            self.last_run[name] = now
            return True
        return False

    def tick(self):
        now = time.time()
        f = self.fetch
        jobs = {
            "news_rss": f.news, "binance_announcements": f.binance_announcements,
            "coingecko_trending": f.coingecko_trending, "cryptopanic": f.cryptopanic,
            "reddit": f.reddit, "fourchan_biz": f.fourchan, "telegram_public": f.telegram,
        }
        for name, job in jobs.items():
            if self.due(name, now):
                self.process(job(), name)
        if self.due("exchanges", now):
            self.check_listings()
        if now - self.last_run.get("_outcomes", 0) > 600:
            self.last_run["_outcomes"] = now
            self.update_outcomes(now)

    def run(self, once: bool = False):
        print("INTEL running: exchange listings, news, aggregators, community. Ctrl+C to stop.")
        while True:
            self.tick()
            if self.fetch.errors:
                print("[sources down] " + " | ".join(f"{k}: {v}" for k, v in self.fetch.errors.items()),
                      file=sys.stderr)
            if once:
                return
            time.sleep(15)


def print_sources(store: Store):
    rows = store.source_stats()
    if not rows:
        print("No token calls recorded yet. Let `python -m memescan intel` run for a day or two.")
        return
    print(f"{'SOURCE':28} {'CALLS':>6} {'CHECKED':>8} {'HITS':>5} {'RUGS':>5} {'EDGE':>6}")
    for r in rows:
        print(f"{r['source'][:28]:28} {r['mentions']:>6} {r['evaluated']:>8} {r['hits']:>5} {r['rugs']:>5} "
              f"{r['edge'] * 100:>5.0f}%")
    print("\nEDGE = chance the source's first call on a coin was followed by +50% in 1h or x2 in 24h "
          "(starts at 50% and moves only with evidence).")
