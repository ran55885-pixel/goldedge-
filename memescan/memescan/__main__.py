"""MemeScan CLI.

  python -m memescan scan     one scan, print the ranked candidates
  python -m memescan run      scan forever + paper trade + alerts
  python -m memescan stats    paper trading results
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import yaml

from .notify import Notifier
from .paper import PaperTrader
from .safety import Safety
from .scoring import hard_filter, score
from .sources import Client

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_cfg(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def fmt_usd(x: float) -> str:
    for unit, div in (("M", 1e6), ("K", 1e3)):
        if abs(x) >= div:
            return f"${x / div:.1f}{unit}"
    return f"${x:.0f}"


def scan_once(cfg: dict, client: Client, safety: Safety, verbose: bool = False):
    """Returns ranked list of (score, pair, breakdown, safety_result)."""
    out = []
    for chain in cfg["chains"]:
        tokens = sorted(client.discover(chain))
        pairs = client.pairs(chain, tokens)
        passed = []
        for p in pairs.values():
            fails = hard_filter(p, cfg["filters"])
            if fails:
                if verbose:
                    print(f"  skip {chain:6} {p.symbol:12} {', '.join(fails)}")
                continue
            s, parts = score(p)
            passed.append((s, p, parts))
        # Safety APIs are slower: only check the ones that passed the filters, best first.
        for s, p, parts in sorted(passed, key=lambda x: -x[0]):
            out.append((s, p, parts, safety.check(chain, p.token)))
        print(f"[{chain}] discovered {len(tokens)} tokens, {len(pairs)} with pairs, "
              f"{len(passed)} passed filters", file=sys.stderr)
    return sorted(out, key=lambda x: -x[0])


def print_table(rows, min_score: float):
    print(f"\n{'SCORE':>5}  {'CHAIN':6} {'SYMBOL':12} {'AGE':>6} {'FDV':>8} {'LIQ':>8} "
          f"{'VOL1h':>8} {'5m%':>6} {'1h%':>6} {'6h%':>7}  SAFETY")
    for s, p, _, sr in rows:
        age = f"{p.age_minutes / 60:.1f}h" if p.age_minutes >= 60 else f"{p.age_minutes:.0f}m"
        flag = "OK" if sr.ok else "X " + "; ".join(sr.reasons[:2])
        mark = ">>" if sr.ok and s >= min_score else "  "
        print(f"{mark}{s:>3.0f}  {p.chain:6} {p.symbol[:12]:12} {age:>6} {fmt_usd(p.fdv_usd):>8} "
              f"{fmt_usd(p.liquidity_usd):>8} {fmt_usd(p.volume.get('h1', 0)):>8} "
              f"{p.price_change.get('m5', 0):>6.0f} {p.price_change.get('h1', 0):>6.0f} "
              f"{p.price_change.get('h6', 0):>7.0f}  {flag}")
    print(f"\n>> = passed safety and score >= {min_score} (would be an entry)\n")


def cmd_scan(cfg, args):
    rows = scan_once(cfg, Client(), Safety(cfg["safety"]), args.verbose)
    print_table(rows[: args.top], cfg["entry_min_score"])


def cmd_run(cfg, args):
    client, safety = Client(), Safety(cfg["safety"])
    trader = PaperTrader(cfg["paper"])
    note = Notifier(cfg.get("telegram", {}).get("enabled", False))
    print("MemeScan running in PAPER mode (simulated trades, no real money). Ctrl+C to stop.")
    while True:
        t0 = time.time()
        try:
            # 1) manage open positions
            for chain in {p.chain for p in trader.positions.values()}:
                held = [t for t, p in trader.positions.items() if p.chain == chain]
                live = client.pairs(chain, held)
                for t in held:
                    if t in live:
                        for ev in trader.update(t, live[t].price_usd, live[t].liquidity_usd):
                            msg = (f"{ev['action']} {ev['symbol']} ({ev['chain']}) "
                                   f"{ev['change_pct']:+.0f}% -> ${ev['proceeds_usd']}"
                                   + (f" | trade PnL {ev['final_pnl_pct']:+}%" if ev["final_pnl_pct"] != "" else ""))
                            print(msg)
                            note.send(msg)
            # 2) look for new entries
            rows = scan_once(cfg, client, safety)
            for s, p, parts, sr in rows:
                if s < cfg["entry_min_score"] or not sr.ok:
                    continue
                pos = trader.enter(p, s) if cfg["paper"]["enabled"] else None
                if pos:
                    msg = (f"PAPER BUY {p.symbol} ({p.chain}) score {s:.0f}\n"
                           f"price ${p.price_usd:.10g} | FDV {fmt_usd(p.fdv_usd)} | liq {fmt_usd(p.liquidity_usd)}\n"
                           f"1h {p.price_change.get('h1', 0):+.0f}% | 5m {p.price_change.get('m5', 0):+.0f}%\n{p.url}")
                    print(msg)
                    note.send(msg)
            st = trader.stats()
            print(f"[{time.strftime('%H:%M:%S')}] open {st['open_positions']} | cash ${st['cash_usd']} "
                  f"| closed {st['closed_trades']} | win {st['win_rate_pct']}% | realized ${st['realized_pnl_usd']}")
        except Exception as e:  # keep the loop alive on network hiccups
            print(f"cycle error: {type(e).__name__}: {e}", file=sys.stderr)
        time.sleep(max(5, cfg["interval_seconds"] - (time.time() - t0)))


def cmd_stats(cfg, args):
    trader = PaperTrader(cfg["paper"])
    prices = {}
    if trader.positions:
        client = Client()
        for chain in {p.chain for p in trader.positions.values()}:
            held = [t for t, p in trader.positions.items() if p.chain == chain]
            prices.update({t: pr.price_usd for t, pr in client.pairs(chain, held).items()})
    st = trader.stats(prices)
    best = st.pop("best_trade")
    for k, v in st.items():
        print(f"{k:18} {v}")
    if best:
        print(f"{'best_trade':18} {best['symbol']} {best['final_pnl_pct']}%  {best['url']}")
    for t, p in trader.positions.items():
        now = prices.get(t, p.entry_price)
        print(f"  open {p.symbol:12} {p.chain:6} {(now / p.entry_price - 1) * 100:+7.1f}%  {p.url}")


def main():
    ap = argparse.ArgumentParser(prog="memescan")
    ap.add_argument("--config", default=os.path.join(HERE, "config.yaml"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--top", type=int, default=25)
    s.add_argument("-v", "--verbose", action="store_true")
    sub.add_parser("run")
    sub.add_parser("stats")
    args = ap.parse_args()
    cfg = load_cfg(args.config)
    {"scan": cmd_scan, "run": cmd_run, "stats": cmd_stats}[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
