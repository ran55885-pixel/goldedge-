"""OANDA v20 adapter (secondary price source). Same prices as TradingView OANDA:XAUUSD.

Endpoints (v20 REST, https://developer.oanda.com/rest-live-v20/):
  GET  {api}/v3/instruments/{instrument}/candles?granularity=M1&count=N&price=MBA
  GET  {api}/v3/accounts/{accountID}/pricing?instruments=XAU_USD,EUR_USD
  GET  {stream}/v3/accounts/{accountID}/pricing/stream?instruments=...   (chunked JSON lines, HEARTBEAT)
  GET  {api}/v3/instruments/{instrument}/positionBook | orderBook       (sentiment, if allowed for the token)
Hosts: practice api-fxpractice.oanda.com / stream-fxpractice.oanda.com,
       live     api-fxtrade.oanda.com   / stream-fxtrade.oanda.com
Token + account id come only from .env (OANDA_TOKEN, OANDA_ACCOUNT_ID, OANDA_ENV).
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime

import httpx
import pandas as pd

from aurum_nexus.data.bars import normalize
from aurum_nexus.data.base import MarketDataAdapter
from aurum_nexus.data.specs import generic_spec
from aurum_nexus.models import SymbolSpec, Tick, empty_bars
from aurum_nexus.symbols import canonical, oanda_instrument

HOSTS = {
    "practice": ("https://api-fxpractice.oanda.com", "https://stream-fxpractice.oanda.com"),
    "live": ("https://api-fxtrade.oanda.com", "https://stream-fxtrade.oanda.com"),
}
GRAN = {"M1": "M1", "M5": "M5", "M15": "M15", "M30": "M30", "H1": "H1", "H4": "H4", "D1": "D", "W1": "W"}


def parse_time(s: str) -> float:
    """Parse OANDA RFC3339 (nanosecond) timestamps or UNIX strings to epoch seconds."""
    if s is None:
        return 0.0
    s = str(s)
    if s.replace(".", "", 1).isdigit():
        return float(s)
    s = s.rstrip("Z")
    if "." in s:
        head, frac = s.split(".", 1)
        frac = (frac + "000000")[:6]
        s = f"{head}.{frac}"
        fmt = "%Y-%m-%dT%H:%M:%S.%f"
    else:
        fmt = "%Y-%m-%dT%H:%M:%S"
    from datetime import timezone
    return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp()


def parse_candles(payload: dict) -> pd.DataFrame:
    rows = []
    for c in payload.get("candles", []):
        src = c.get("mid") or c.get("bid") or c.get("ask")
        if not src:
            continue
        spread = 0.0
        if c.get("bid") and c.get("ask"):
            spread = float(c["ask"]["c"]) - float(c["bid"]["c"])
        rows.append({"time": int(parse_time(c["time"])), "open": float(src["o"]), "high": float(src["h"]),
                     "low": float(src["l"]), "close": float(src["c"]), "tick_volume": float(c.get("volume", 0)),
                     "real_volume": 0.0, "spread": spread, "complete": bool(c.get("complete", True))})
    if not rows:
        return empty_bars()
    return normalize(pd.DataFrame(rows))


def parse_price(obj: dict) -> Tick | None:
    if obj.get("type") not in (None, "PRICE"):
        return None
    bids, asks = obj.get("bids") or [], obj.get("asks") or []
    if not bids or not asks:
        return None
    instr = obj.get("instrument", "")
    sym = canonical(instr.replace("_", ""))
    return Tick(sym, parse_time(obj.get("time")), float(bids[0]["price"]), float(asks[0]["price"]), source="OANDA")


class OandaAdapter(MarketDataAdapter):
    kind = "oanda"
    is_preview = False

    def __init__(self, token: str | None = None, account_id: str | None = None, env: str | None = None,
                 client: httpx.Client | None = None, timeout: float = 10.0):
        super().__init__()
        self.token = token if token is not None else os.environ.get("OANDA_TOKEN", "")
        self.account_id = account_id if account_id is not None else os.environ.get("OANDA_ACCOUNT_ID", "")
        self.env = (env or os.environ.get("OANDA_ENV", "practice") or "practice").lower()
        if self.env not in HOSTS:
            self.env = "practice"
        self.api, self.stream_host = HOSTS[self.env]
        self.client = client or httpx.Client(timeout=timeout)
        self.label = f"OANDA v20 ({self.env})"
        self._ticks: dict[str, Tick] = {}
        self._stream_thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def configured(self) -> bool:
        return bool(self.token and self.account_id)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Accept-Datetime-Format": "RFC3339"}

    def _get(self, url: str, params: dict | None = None) -> dict:
        r = self.client.get(url, params=params, headers=self._headers())
        if r.status_code != 200:
            raise RuntimeError(f"OANDA HTTP {r.status_code}: {r.text[:200]}")
        return r.json()

    def connect(self) -> bool:
        if not self.configured:
            self.last_error = "OANDA_TOKEN / OANDA_ACCOUNT_ID not set"
            self.connected = False
            return False
        try:
            self._get(f"{self.api}/v3/accounts/{self.account_id}/summary")
            self.connected = True
            self.last_error = ""
        except Exception as e:
            self.last_error = str(e)[:200]
            self.connected = False
        return self.connected

    def resolve_symbol(self, canonical_name: str) -> str | None:
        return oanda_instrument(canonical_name)

    def symbol_spec(self, canonical_name: str) -> SymbolSpec | None:
        return generic_spec(canonical_name)

    def tick(self, canonical_name: str) -> Tick | None:
        t = self._ticks.get(canonical_name)
        if t and time.time() - t.time < 2 and self._stream_thread and self._stream_thread.is_alive():
            return t
        try:
            data = self._get(f"{self.api}/v3/accounts/{self.account_id}/pricing",
                             {"instruments": oanda_instrument(canonical_name)})
            for p in data.get("prices", []):
                tk = parse_price(p)
                if tk:
                    tk.symbol = canonical_name
                    self._ticks[canonical_name] = tk
                    self.last_tick_time[canonical_name] = tk.time
                    return tk
        except Exception as e:
            self.last_error = str(e)[:200]
        return self._ticks.get(canonical_name)

    def bars(self, canonical_name: str, tf: str, count: int) -> pd.DataFrame:
        params = {"granularity": GRAN[tf], "count": min(int(count), 5000), "price": "MBA"}
        if tf in ("D1", "W1"):
            params.update({"dailyAlignment": 17, "alignmentTimezone": "America/New_York"})
        try:
            data = self._get(f"{self.api}/v3/instruments/{oanda_instrument(canonical_name)}/candles", params)
            return parse_candles(data)
        except Exception as e:
            self.last_error = str(e)[:200]
            return empty_bars()

    def has_volume(self, canonical_name: str) -> bool:
        return True  # OANDA candle volume = tick count

    # ------------------------------------------------------------------ streaming
    def start_stream(self, symbols: list[str]) -> None:
        if not self.configured or (self._stream_thread and self._stream_thread.is_alive()):
            return
        self._stop.clear()
        self._stream_thread = threading.Thread(target=self._stream_loop, args=(symbols,), daemon=True,
                                               name="oanda-stream")
        self._stream_thread.start()

    def stop_stream(self) -> None:
        self._stop.set()

    def handle_stream_line(self, line: str) -> Tick | None:
        line = line.strip()
        if not line:
            return None
        obj = json.loads(line)
        if obj.get("type") == "HEARTBEAT":
            return None
        tk = parse_price(obj)
        if tk:
            self._ticks[tk.symbol] = tk
            self.last_tick_time[tk.symbol] = tk.time
        return tk

    def _stream_loop(self, symbols: list[str]) -> None:
        backoff = 1.0
        instruments = ",".join(oanda_instrument(s) for s in symbols)
        url = f"{self.stream_host}/v3/accounts/{self.account_id}/pricing/stream"
        while not self._stop.is_set():
            try:
                with httpx.stream("GET", url, params={"instruments": instruments}, headers=self._headers(),
                                  timeout=httpx.Timeout(30.0, read=30.0)) as r:
                    if r.status_code != 200:
                        raise RuntimeError(f"stream HTTP {r.status_code}")
                    backoff = 1.0
                    for line in r.iter_lines():
                        if self._stop.is_set():
                            return
                        self.handle_stream_line(line)
            except Exception as e:
                self.last_error = f"stream: {str(e)[:150]}"
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 60.0)

    # ------------------------------------------------------------------ sentiment books
    def position_book(self, canonical_name: str) -> dict | None:
        try:
            data = self._get(f"{self.api}/v3/instruments/{oanda_instrument(canonical_name)}/positionBook")
            book = data.get("positionBook") or {}
            buckets = book.get("buckets") or []
            longp = sum(float(b.get("longCountPercent", 0)) for b in buckets)
            shortp = sum(float(b.get("shortCountPercent", 0)) for b in buckets)
            tot = longp + shortp
            if tot <= 0:
                return None
            return {"long_pct": 100 * longp / tot, "short_pct": 100 * shortp / tot, "time": book.get("time"),
                    "source": "OANDA positionBook"}
        except Exception as e:
            self.last_error = str(e)[:200]
            return None
