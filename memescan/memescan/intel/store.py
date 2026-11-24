"""SQLite store: seen items, token mentions, their price outcomes, and per-source reliability."""
from __future__ import annotations

import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY, ts REAL);
CREATE TABLE IF NOT EXISTS baseline (exchange TEXT, symbol TEXT, PRIMARY KEY (exchange, symbol));
CREATE TABLE IF NOT EXISTS mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT, group_name TEXT, chain TEXT, token TEXT, symbol TEXT,
    ts REAL, price REAL, price_1h REAL, price_24h REAL, url TEXT,
    UNIQUE (source, token)
);
CREATE INDEX IF NOT EXISTS mentions_token ON mentions (token, ts);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, source TEXT, title TEXT, url TEXT, important INTEGER
);
"""

# A mention "hit" if the price is up this much later; "rug" if down this much.
HIT_1H, HIT_24H, RUG = 0.5, 1.0, -0.8


class Store:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.executescript(SCHEMA)

    # ---------- dedupe ----------
    def is_new(self, key: str) -> bool:
        cur = self.db.execute("INSERT OR IGNORE INTO seen (key, ts) VALUES (?, ?)", (key, time.time()))
        self.db.commit()
        return cur.rowcount == 1

    def baseline(self, exchange: str) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT symbol FROM baseline WHERE exchange=?", (exchange,))}

    def add_baseline(self, exchange: str, symbols: set[str]):
        self.db.executemany("INSERT OR IGNORE INTO baseline VALUES (?, ?)", [(exchange, s) for s in symbols])
        self.db.commit()

    # ---------- events ----------
    def log_event(self, kind: str, source: str, title: str, url: str, important: bool):
        self.db.execute("INSERT INTO events (ts, kind, source, title, url, important) VALUES (?,?,?,?,?,?)",
                        (time.time(), kind, source, title, url, int(important)))
        self.db.commit()

    # ---------- mentions ----------
    def add_mention(self, source: str, group: str, chain: str, token: str, symbol: str,
                    price: float, url: str, ts: float | None = None) -> bool:
        """First mention of a token by a source counts; repeats by the same source don't."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO mentions (source, group_name, chain, token, symbol, ts, price, url) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (source, group, chain, token, symbol, time.time() if ts is None else ts, price, url))
        self.db.commit()
        return cur.rowcount == 1

    def buzz(self, token: str, window_minutes: float, now: float | None = None) -> list[tuple[str, str]]:
        """Independent sources (source, group) that mentioned the token inside the window."""
        since = (time.time() if now is None else now) - window_minutes * 60
        return list(self.db.execute(
            "SELECT DISTINCT source, group_name FROM mentions WHERE token=? AND ts>=?", (token, since)))

    def buzz_count(self, token: str, window_minutes: float) -> int:
        return len(self.buzz(token, window_minutes))

    def pending_outcomes(self, now: float | None = None) -> list[tuple]:
        now = time.time() if now is None else now
        return list(self.db.execute(
            "SELECT id, chain, token, ts, price_1h, price_24h FROM mentions WHERE price > 0 AND ("
            "(price_1h IS NULL AND ts <= ?) OR (price_24h IS NULL AND ts <= ?))", (now - 3600, now - 86400)))

    def set_outcome(self, mention_id: int, field: str, price: float):
        assert field in ("price_1h", "price_24h")
        self.db.execute(f"UPDATE mentions SET {field}=? WHERE id=?", (price, mention_id))
        self.db.commit()

    # ---------- reliability ----------
    def source_stats(self) -> list[dict]:
        """Per source: how often its first call on a token was followed by a pump or a rug.
        edge = (hits + 1) / (evaluated + 2), a Laplace estimate that starts at 50% and needs evidence."""
        rows = self.db.execute("SELECT source, price, price_1h, price_24h FROM mentions WHERE price > 0").fetchall()
        stats: dict[str, dict] = {}
        for src, p0, p1, p24 in rows:
            s = stats.setdefault(src, {"source": src, "mentions": 0, "evaluated": 0, "hits": 0, "rugs": 0})
            s["mentions"] += 1
            if p1 is None and p24 is None:
                continue
            s["evaluated"] += 1
            r1 = (p1 / p0 - 1) if p1 is not None else None
            r24 = (p24 / p0 - 1) if p24 is not None else None
            if (r1 is not None and r1 >= HIT_1H) or (r24 is not None and r24 >= HIT_24H):
                s["hits"] += 1
            last = r24 if r24 is not None else r1
            if last is not None and last <= RUG:
                s["rugs"] += 1
        for s in stats.values():
            s["edge"] = round((s["hits"] + 1) / (s["evaluated"] + 2), 3)
            s["rug_rate"] = round(s["rugs"] / s["evaluated"], 3) if s["evaluated"] else None
        return sorted(stats.values(), key=lambda s: (-s["edge"], -s["evaluated"]))
