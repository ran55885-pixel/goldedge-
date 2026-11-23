"""Launch mode: catch brand-new pump.fun coins in their first minutes.

Live feed: PumpPortal websocket (new token + migration events are free; per-trade events
need an API key in MEMESCAN_PUMPPORTAL_KEY). Stats for watched coins: DexScreener, polled.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field

import websockets

from .notify import Notifier
from .paper import DATA_DIR, PaperTrader
from .safety import Safety
from .sources import Client, Pair

WS_URL = "wss://pumpportal.fun/api/data"
PUMP_SUPPLY = 1_000_000_000  # every pump.fun coin has 1B tokens


@dataclass
class Launch:
    mint: str
    symbol: str
    name: str
    creator: str
    created_at: float
    dev_buy_pct: float
    launch_mcap_sol: float
    migrated: bool = False
    buyers: set = field(default_factory=set)
    dev_sold: bool = False
    peak_multiple: float = 1.0
    signaled: bool = False


class LaunchTracker:
    def __init__(self, cfg: dict, has_trades: bool = False):
        self.cfg = cfg
        self.has_trades = has_trades
        self.watch: dict[str, Launch] = {}
        self.launches_seen = 0
        self.migrations_seen = 0

    # ---------- feed events ----------
    def on_message(self, m: dict, now: float | None = None) -> str | None:
        now = time.time() if now is None else now
        tx = m.get("txType")
        if tx == "create":
            self.launches_seen += 1
            self.watch[m["mint"]] = Launch(
                mint=m["mint"], symbol=m.get("symbol", "?"), name=m.get("name", "?"),
                creator=m.get("traderPublicKey", ""), created_at=now,
                dev_buy_pct=float(m.get("initialBuy") or 0) / PUMP_SUPPLY * 100,
                launch_mcap_sol=float(m.get("marketCapSol") or 0))
            return "create"
        launch = self.watch.get(m.get("mint", ""))
        if tx == "migrate":
            self.migrations_seen += 1
            if launch:
                launch.migrated = True
            return "migrate"
        if launch and tx in ("buy", "sell"):
            trader = m.get("traderPublicKey", "")
            if tx == "buy":
                launch.buyers.add(trader)
            elif trader == launch.creator:
                launch.dev_sold = True
            return tx
        return None

    def expire(self, now: float, keep: set[str] = frozenset()):
        limit = self.cfg["watch_minutes"] * 60
        for mint in [k for k, v in self.watch.items() if now - v.created_at > limit and k not in keep]:
            del self.watch[mint]

    def ready(self, now: float) -> list[str]:
        return [k for k, v in self.watch.items()
                if now - v.created_at >= self.cfg["min_age_seconds"] and not v.signaled]

    # ---------- decision ----------
    def multiple(self, launch: Launch, p: Pair) -> float:
        if p.quote_symbol.upper() not in ("SOL", "WSOL") or launch.launch_mcap_sol <= 0:
            return 0.0
        return p.price_native * PUMP_SUPPLY / launch.launch_mcap_sol

    def evaluate(self, launch: Launch, p: Pair) -> tuple[list[str], float, dict]:
        c = self.cfg
        mult = self.multiple(launch, p)
        launch.peak_multiple = max(launch.peak_multiple, mult)
        buys, sells = p.buys("m5"), p.sells("m5")
        ratio = buys / max(buys + sells, 1)
        chg = p.price_change.get("m5", 0)
        vol = p.volume.get("m5", 0)

        fails = []
        if launch.dev_buy_pct > c["max_dev_buy_pct"]:
            fails.append(f"dev holds {launch.dev_buy_pct:.0f}%")
        if mult < c["min_mcap_multiple"]:
            fails.append(f"only x{mult:.1f}")
        if not c["min_mcap_usd"] <= p.fdv_usd <= c["max_mcap_usd"]:
            fails.append("mcap out of range")
        if buys < c["min_buys_m5"]:
            fails.append("few buys")
        if ratio < c["min_buy_ratio_m5"]:
            fails.append("sellers dominate")
        if vol < c["min_volume_m5_usd"]:
            fails.append("low volume")
        if chg < c["min_price_change_m5"]:
            fails.append("not climbing now")
        if self.has_trades:
            if len(launch.buyers) < c["min_unique_buyers"]:
                fails.append("few unique buyers")
            if launch.dev_sold:
                fails.append("dev sold")

        clamp = lambda x: max(0.0, min(1.0, x))  # noqa: E731
        parts = {
            "multiple": clamp(math.log10(max(mult, 1))),  # log scale: x2=0.3, x5=0.7, x10=full
            "buys": clamp(buys / 200),
            "buy_ratio": clamp((ratio - 0.5) / 0.25),
            "volume": clamp(vol / 30_000),
            "climbing": clamp(chg / 100),
            "dev_small": clamp(1 - launch.dev_buy_pct / 10),
        }
        weights = {"multiple": 25, "buys": 15, "buy_ratio": 20, "volume": 15, "climbing": 15, "dev_small": 10}
        s = sum(parts[k] * w for k, w in weights.items()) + (10 if launch.migrated else 0)
        return fails, round(min(100.0, s), 1), {k: round(v, 2) for k, v in parts.items()}


# ---------------------------------------------------------------------------
async def feed(tracker: LaunchTracker, key: str | None, subscribe_q: asyncio.Queue):
    """Read the PumpPortal websocket forever, reconnecting on errors."""
    url = WS_URL + (f"?api-key={key}" if key else "")
    backoff = 1
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, max_size=2**20) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                await ws.send(json.dumps({"method": "subscribeMigration"}))
                if key and tracker.watch:
                    await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": list(tracker.watch)}))
                backoff = 1
                while True:
                    while not subscribe_q.empty():
                        method, keys = subscribe_q.get_nowait()
                        await ws.send(json.dumps({"method": method, "keys": keys}))
                    try:
                        raw = await asyncio.wait_for(ws.recv(), 5)
                    except asyncio.TimeoutError:
                        continue
                    m = json.loads(raw)
                    if "message" in m and "txType" not in m:
                        if "only available" in m["message"] or "error" in m["message"].lower():
                            print(f"[feed] {m['message']}", file=sys.stderr)
                        continue
                    if tracker.on_message(m) == "create" and key:
                        await subscribe_q.put(("subscribeTokenTrade", [m["mint"]]))
        except Exception as e:  # noqa: BLE001
            print(f"[feed] disconnected ({type(e).__name__}), retry in {backoff}s", file=sys.stderr)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


async def brain(cfg: dict, tracker: LaunchTracker, subscribe_q: asyncio.Queue):
    lc = cfg["launch"]
    client, safety = Client(), Safety(cfg["safety"])
    trader = PaperTrader(lc["paper"], os.path.join(DATA_DIR, "launch"))
    note = Notifier(cfg.get("telegram", {}).get("enabled", False))
    last_status = 0.0

    def say(msg: str):
        print(msg, flush=True)
        note.send(msg)

    while True:
        await asyncio.sleep(lc["poll_seconds"])
        now = time.time()
        try:
            # 1) refresh stats for ready launches + open positions in one batched call
            mints = sorted(set(tracker.ready(now)) | set(trader.positions))
            pairs = await asyncio.to_thread(client.pairs, "solana", mints) if mints else {}

            # 2) manage open positions
            for mint in list(trader.positions):
                p = pairs.get(mint)
                if not p:
                    continue
                launch = tracker.watch.get(mint)
                if launch and launch.dev_sold and lc.get("exit_if_dev_sells", True):
                    events = trader.force_exit(mint, p.price_usd, "EXIT dev sold")
                else:
                    events = trader.update(mint, p.price_usd, p.liquidity_usd)
                for ev in events:
                    tail = f" | trade PnL {ev['final_pnl_pct']:+}%" if ev["final_pnl_pct"] != "" else ""
                    say(f"{ev['action']} {ev['symbol']} {ev['change_pct']:+.0f}% -> ${ev['proceeds_usd']}{tail}")

            # 3) look for entries
            for mint in tracker.ready(now):
                launch, p = tracker.watch[mint], pairs.get(mint)
                if not p:
                    continue
                fails, s, _ = tracker.evaluate(launch, p)
                if fails or s < lc["entry_min_score"]:
                    continue
                launch.signaled = True
                if lc.get("require_rugcheck", True):
                    sr = await asyncio.to_thread(safety.check, "solana", mint)
                    if not sr.ok:
                        print(f"  blocked {launch.symbol}: {'; '.join(sr.reasons)}")
                        continue
                age = (now - launch.created_at) / 60
                pos = trader.enter(p, s, now)
                head = "PAPER BUY" if pos else f"SIGNAL (no entry: {trader.can_enter(mint)})"
                say(f"{head} {launch.symbol} | score {s:.0f} | {age:.1f} min old | "
                    f"x{tracker.multiple(launch, p):.1f} from launch | mcap ${p.fdv_usd:,.0f}\n"
                    f"5m: {p.buys('m5')} buys / {p.sells('m5')} sells, {p.price_change.get('m5', 0):+.0f}% | "
                    f"dev {launch.dev_buy_pct:.1f}%{' | GRADUATED' if launch.migrated else ''}\n{p.url}")

            tracker.expire(now, keep=set(trader.positions))
            if now - last_status > 60:
                last_status = now
                hot = sorted(tracker.watch.values(), key=lambda v: -v.peak_multiple)[:3]
                st = trader.stats()
                print(f"[{time.strftime('%H:%M:%S')}] launches {tracker.launches_seen} | watching "
                      f"{len(tracker.watch)} | graduated {tracker.migrations_seen} | hottest: "
                      + ", ".join(f"{h.symbol} x{h.peak_multiple:.1f}" for h in hot)
                      + f" | open {st['open_positions']} | closed {st['closed_trades']} "
                        f"| win {st['win_rate_pct']}% | PnL ${st['realized_pnl_usd']}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"cycle error: {type(e).__name__}: {e}", file=sys.stderr)


async def run_launch(cfg: dict, duration: float | None = None):
    key = os.environ.get("MEMESCAN_PUMPPORTAL_KEY")
    tracker = LaunchTracker(cfg["launch"], has_trades=bool(key))
    q: asyncio.Queue = asyncio.Queue()
    print("LAUNCH mode, PAPER trading (no real money). Per-trade data: "
          + ("ON" if key else "OFF (set MEMESCAN_PUMPPORTAL_KEY for unique buyers + dev-sell detection)"))
    tasks = [asyncio.create_task(feed(tracker, key, q)), asyncio.create_task(brain(cfg, tracker, q))]
    try:
        await asyncio.wait(tasks, timeout=duration)
    finally:
        for t in tasks:
            t.cancel()
