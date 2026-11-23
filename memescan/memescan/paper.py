"""Paper trading: simulated entries/exits with stop loss, partial take-profit, trailing stop, rug exit."""
from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import asdict, dataclass

DATA_DIR = os.environ.get("MEMESCAN_DATA", os.path.join(os.path.dirname(__file__), "..", "data"))


@dataclass
class Position:
    chain: str
    token: str
    symbol: str
    url: str
    entry_price: float
    entry_liquidity: float
    cost_usd: float          # USD put in
    qty: float               # tokens held now
    opened_at: float
    score: float
    peak_price: float
    tp_done: bool = False
    realized_usd: float = 0.0  # USD already taken out


class PaperTrader:
    def __init__(self, cfg: dict, data_dir: str = DATA_DIR):
        self.cfg = cfg
        self.dir = data_dir
        os.makedirs(self.dir, exist_ok=True)
        self.state_path = os.path.join(self.dir, "paper_state.json")
        self.log_path = os.path.join(self.dir, "paper_trades.csv")
        self.balance = float(cfg["starting_balance_usd"])
        self.positions: dict[str, Position] = {}
        self.entries_by_day: dict[str, int] = {}
        self.traded: set[str] = set()  # never re-enter the same token
        self._load()

    # ---------- persistence ----------
    def _load(self):
        if not os.path.exists(self.state_path):
            return
        with open(self.state_path) as f:
            s = json.load(f)
        self.balance = s["balance"]
        self.positions = {k: Position(**v) for k, v in s["positions"].items()}
        self.entries_by_day = s.get("entries_by_day", {})
        self.traded = set(s.get("traded", []))

    def save(self):
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"balance": self.balance,
                       "positions": {k: asdict(v) for k, v in self.positions.items()},
                       "entries_by_day": self.entries_by_day,
                       "traded": sorted(self.traded)}, f, indent=2)
        os.replace(tmp, self.state_path)

    def _log(self, row: dict):
        new = not os.path.exists(self.log_path)
        with open(self.log_path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(row))
            if new:
                w.writeheader()
            w.writerow(row)

    # ---------- entries ----------
    def can_enter(self, token: str) -> str | None:
        """None if allowed, otherwise the reason."""
        c = self.cfg
        day = time.strftime("%Y-%m-%d", time.gmtime())
        if token in self.traded:
            return "already traded"
        if len(self.positions) >= c["max_open_positions"]:
            return "max open positions"
        if self.entries_by_day.get(day, 0) >= c["max_entries_per_day"]:
            return "daily entry limit"
        if self.balance < c["position_size_usd"]:
            return "no balance"
        return None

    def enter(self, pair, score: float, now: float | None = None) -> Position | None:
        if self.can_enter(pair.token) or pair.price_usd <= 0:
            return None
        now = time.time() if now is None else now
        size = self.cfg["position_size_usd"]
        fill = pair.price_usd * (1 + self.cfg["slippage_pct"] / 100)
        pos = Position(pair.chain, pair.token, pair.symbol, pair.url, fill, pair.liquidity_usd,
                       size, size / fill, now, score, fill)
        self.balance -= size
        self.positions[pair.token] = pos
        self.traded.add(pair.token)
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        self.entries_by_day[day] = self.entries_by_day.get(day, 0) + 1
        self.save()
        return pos

    # ---------- exits ----------
    def _sell(self, pos: Position, fraction: float, price: float, reason: str, now: float) -> dict:
        fill = price * (1 - self.cfg["slippage_pct"] / 100)
        qty = pos.qty * fraction
        proceeds = qty * fill
        pos.qty -= qty
        pos.realized_usd += proceeds
        self.balance += proceeds
        event = {"time": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)), "chain": pos.chain,
                 "symbol": pos.symbol, "token": pos.token, "action": reason,
                 "fraction": round(fraction, 3), "price": f"{fill:.10g}",
                 "entry": f"{pos.entry_price:.10g}", "change_pct": round((fill / pos.entry_price - 1) * 100, 1),
                 "proceeds_usd": round(proceeds, 2), "score": pos.score, "url": pos.url}
        if pos.qty <= 1e-12:
            pnl = pos.realized_usd - pos.cost_usd
            event["final_pnl_usd"] = round(pnl, 2)
            event["final_pnl_pct"] = round(pnl / pos.cost_usd * 100, 1)
            del self.positions[pos.token]
        else:
            event["final_pnl_usd"] = event["final_pnl_pct"] = ""
        self._log(event)
        return event

    def update(self, token: str, price: float, liquidity: float, now: float | None = None) -> list[dict]:
        """Apply exit rules to one open position. Returns sell events."""
        pos = self.positions.get(token)
        if not pos or price <= 0:
            return []
        c, now = self.cfg, (time.time() if now is None else now)
        pos.peak_price = max(pos.peak_price, price)
        change = (price / pos.entry_price - 1) * 100
        events = []

        if pos.entry_liquidity > 0 and liquidity < pos.entry_liquidity * (1 - c["rug_liquidity_drop_pct"] / 100):
            events.append(self._sell(pos, 1.0, price, "EXIT rug (liquidity pulled)", now))
        elif change <= -c["stop_loss_pct"]:
            events.append(self._sell(pos, 1.0, price, "EXIT stop loss", now))
        else:
            if not pos.tp_done and change >= c["take_profit_pct"]:
                pos.tp_done = True
                events.append(self._sell(pos, c["take_profit_sell_fraction"], price, "TAKE PROFIT", now))
            if token in self.positions and pos.tp_done and price <= pos.peak_price * (1 - c["trailing_stop_pct"] / 100):
                events.append(self._sell(pos, 1.0, price, "EXIT trailing stop", now))
            elif token in self.positions and now - pos.opened_at > c["max_hold_hours"] * 3600:
                events.append(self._sell(pos, 1.0, price, "EXIT time limit", now))
        self.save()
        return events

    def force_exit(self, token: str, price: float, reason: str, now: float | None = None) -> list[dict]:
        pos = self.positions.get(token)
        if not pos or price <= 0:
            return []
        ev = [self._sell(pos, 1.0, price, reason, time.time() if now is None else now)]
        self.save()
        return ev

    # ---------- reporting ----------
    def stats(self, prices: dict[str, float] | None = None) -> dict:
        closed = []
        if os.path.exists(self.log_path):
            with open(self.log_path) as f:
                closed = [r for r in csv.DictReader(f) if r.get("final_pnl_usd")]
        pnls = [float(r["final_pnl_usd"]) for r in closed]
        open_value = sum(p.qty * (prices or {}).get(t, p.entry_price) for t, p in self.positions.items())
        equity = self.balance + open_value
        return {
            "closed_trades": len(pnls),
            "wins": sum(1 for x in pnls if x > 0),
            "win_rate_pct": round(100 * sum(1 for x in pnls if x > 0) / len(pnls), 1) if pnls else 0,
            "realized_pnl_usd": round(sum(pnls), 2),
            "best_trade": max(closed, key=lambda r: float(r["final_pnl_pct"]), default=None),
            "open_positions": len(self.positions),
            "cash_usd": round(self.balance, 2),
            "equity_usd": round(equity, 2),
            "return_pct": round((equity / self.cfg["starting_balance_usd"] - 1) * 100, 1),
        }
