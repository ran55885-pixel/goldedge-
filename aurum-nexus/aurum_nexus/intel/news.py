"""Economic calendar (ForexFactory weekly JSON, https://nfs.faireconomy.media/ff_calendar_thisweek.json).

Cached to disk; high-impact events for the symbol's currencies (USD for gold/silver) create a
blackout X minutes before/after. Manual blackout windows from config are always applied (fallback
when the feed is unavailable)."""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

from aurum_nexus.symbols import legs


def parse_events(payload: list) -> list[dict]:
    out = []
    for e in payload or []:
        try:
            ts = datetime.fromisoformat(e["date"]).timestamp()
        except (KeyError, ValueError, TypeError):
            continue
        out.append({"title": e.get("title", ""), "country": (e.get("country") or "").upper(), "ts": ts,
                    "impact": e.get("impact", ""), "forecast": e.get("forecast", ""), "previous": e.get("previous", "")})
    return sorted(out, key=lambda x: x["ts"])


def symbol_currencies(symbol: str) -> set[str]:
    b, q = legs(symbol)
    cur = {c for c in (b, q) if c and c not in ("XAU", "XAG")}
    if b in ("XAU", "XAG"):
        cur.add("USD")
    return cur


class NewsCalendar:
    def __init__(self, cfg, cache_dir: Path | None = None, client: httpx.Client | None = None, clock=time.time):
        self.nc = cfg.get("news")
        self.cache = (cache_dir / "news_cache.json") if cache_dir else None
        self.client = client
        self.clock = clock
        self.events: list[dict] = []
        self.fetched_at = 0.0
        self.status = "n/a"
        self.error = ""
        self._lock = threading.Lock()
        self._load_cache()

    def _load_cache(self) -> None:
        if self.cache and self.cache.exists():
            try:
                data = json.loads(self.cache.read_text(encoding="utf-8"))
                self.events = data.get("events", [])
                self.fetched_at = float(data.get("fetched_at", 0))
                self.status = "cached"
            except (ValueError, OSError):
                pass

    def refresh(self, force: bool = False) -> None:
        if not self.nc.get("enabled", True):
            self.status = "disabled"
            return
        now = self.clock()
        if not force and now - self.fetched_at < 60 * float(self.nc["refresh_minutes"]) and self.events:
            return
        try:
            client = self.client or httpx.Client(timeout=15.0, headers={"User-Agent": "AurumNexus/1.0"})
            r = client.get(self.nc["url"])
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            evs = parse_events(r.json())
            with self._lock:
                self.events = evs
                self.fetched_at = now
                self.status = "ok"
                self.error = ""
            if self.cache:
                self.cache.parent.mkdir(parents=True, exist_ok=True)
                self.cache.write_text(json.dumps({"fetched_at": now, "events": evs}), encoding="utf-8")
        except Exception as e:
            self.error = str(e)[:200]
            self.status = "stale cache" if self.events else "unavailable"

    def _manual(self, symbol: str, now: float) -> dict | None:
        cur = symbol_currencies(symbol)
        for b in self.nc.get("manual_blackouts") or []:
            try:
                s = datetime.fromisoformat(str(b["start"]).replace("Z", "+00:00")).timestamp()
                e = datetime.fromisoformat(str(b["end"]).replace("Z", "+00:00")).timestamp()
            except (KeyError, ValueError):
                continue
            curs = set(b.get("currencies") or cur)
            if s <= now <= e and curs & cur:
                return {"title": b.get("title", "manual blackout"), "country": ",".join(sorted(curs)), "ts": s,
                        "manual": True}
        return None

    def blackout(self, symbol: str, now: float) -> dict | None:
        m = self._manual(symbol, now)
        if m:
            return m
        cur = symbol_currencies(symbol)
        before = 60 * float(self.nc["minutes_before"])
        after = 60 * float(self.nc["minutes_after"])
        for e in self.events:
            if e["impact"] in self.nc["impacts"] and e["country"] in cur and e["ts"] - before <= now <= e["ts"] + after:
                return e
        return None

    def upcoming(self, symbol: str, now: float, minutes: int) -> dict | None:
        cur = symbol_currencies(symbol)
        for e in self.events:
            if e["impact"] in self.nc["impacts"] and e["country"] in cur and 0 <= e["ts"] - now <= minutes * 60:
                return {**e, "minutes": (e["ts"] - now) / 60.0}
        return None

    def unavailable_blocks(self) -> bool:
        return self.status == "unavailable" and self.nc.get("on_unavailable") == "block"

    def snapshot(self, now: float, limit: int = 12) -> dict:
        nxt = [e for e in self.events if e["ts"] >= now - 3600 and e["impact"] in ("High", "Medium")][:limit]
        return {"status": self.status, "error": self.error, "fetched_at": self.fetched_at, "next": nxt}
