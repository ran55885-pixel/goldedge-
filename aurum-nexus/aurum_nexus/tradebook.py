"""TradeBook - in-memory + SQLite record of every trade the system opened (paper and live)."""
from __future__ import annotations

import threading
import uuid

from aurum_nexus.timeutil import broker_day_start


class TradeBook:
    def __init__(self, storage, day_boundary: dict | None = None):
        self.storage = storage
        self.day_boundary = day_boundary
        self.open: dict[str, dict] = {}
        self._closed_cache: list[dict] = []
        self._lock = threading.RLock()
        for t in storage.trades(status="open"):
            self.open[t["id"]] = t
        self._closed_cache = storage.trades(status="closed", limit=2000)

    def new_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def add(self, t: dict) -> None:
        with self._lock:
            self.open[t["id"]] = t
        self.storage.upsert_trade(t)

    def save(self, t: dict) -> None:
        self.storage.upsert_trade(t)

    def close(self, t: dict) -> None:
        with self._lock:
            self.open.pop(t["id"], None)
            self._closed_cache.insert(0, t)
            self._closed_cache = self._closed_cache[:2000]
        self.storage.upsert_trade(t)

    def open_for(self, account: str | None = None, symbol: str | None = None) -> list[dict]:
        with self._lock:
            return [t for t in self.open.values() if (account is None or t["account"] == account)
                    and (symbol is None or t["symbol"] == symbol)]

    def closed(self, account: str | None = None, since: float = 0.0, mode: str | None = None) -> list[dict]:
        with self._lock:
            return [t for t in self._closed_cache if (account is None or t["account"] == account)
                    and (t.get("close_ts") or 0) >= since and (mode is None or t.get("mode") == mode)]

    def opened_since(self, account: str, since: float, symbol: str | None = None) -> list[dict]:
        with self._lock:
            allt = list(self.open.values()) + self._closed_cache
        return [t for t in allt if t["account"] == account and (t.get("open_ts") or 0) >= since
                and (symbol is None or t["symbol"] == symbol)]

    def day_start(self, now: float) -> float:
        return broker_day_start(now, self.day_boundary)

    def realized_r_today(self, account: str, now: float) -> list[float]:
        ds = self.day_start(now)
        rows = sorted(self.closed(account, ds), key=lambda t: t.get("close_ts") or 0)
        return [float(t.get("r_multiple") or 0.0) for t in rows]

    def consecutive_losses(self, account: str, since: float) -> int:
        n = 0
        for t in self.closed(account, since):  # newest first
            if (t.get("r_multiple") or 0) < 0:
                n += 1
            else:
                break
        return n

    def last_stopout(self, account: str, symbol: str) -> float | None:
        for t in self.closed(account):
            if t["symbol"] == symbol and t.get("exit_type") in ("sl",) and (t.get("r_multiple") or 0) < 0:
                return t.get("close_ts")
        return None

    def active_zone_ids(self, symbol: str) -> set:
        with self._lock:
            return {(t.get("meta") or {}).get("zone_id") for t in self.open.values() if t["symbol"] == symbol}
