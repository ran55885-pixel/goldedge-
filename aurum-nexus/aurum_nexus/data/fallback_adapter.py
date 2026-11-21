"""Keyless public fallback feed - PREVIEW ONLY, never for live trading.

Live quotes : Swissquote public spot feed
              https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD
History     : Yahoo Finance chart API (v8/finance/chart). Gold/silver use COMEX futures (GC=F / SI=F)
              rebased to spot with a measured basis offset (spot mid - futures close at the same time).
              FX history from Yahoo has no volume -> volume tools disabled for those symbols.
Bars after the last (delayed) history bar are built from collected live quotes (no volume).
Every output carries the label "PREVIEW ONLY · not for trading · delayed Xs".
"""
from __future__ import annotations

import statistics
import threading
import time
from collections import deque

import httpx
import pandas as pd

from aurum_nexus.data.bars import normalize, resample
from aurum_nexus.data.base import MarketDataAdapter
from aurum_nexus.data.specs import generic_spec
from aurum_nexus.models import TF_SECONDS, SymbolSpec, Tick, empty_bars
from aurum_nexus.symbols import canonical, swissquote_instrument, yahoo_ticker

SQ_URL = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/{inst}"
YH_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
UA = "Mozilla/5.0 (compatible; AurumNexus/1.0; +preview)"

# interval, range, cache ttl seconds
YH_PLAN = {"M1": ("1m", "5d", 45), "M5": ("5m", "1mo", 90), "M15": ("15m", "1mo", 180),
           "M30": ("30m", "1mo", 300), "H1": ("60m", "6mo", 600)}
FUTURES_BASED = {"XAUUSD", "XAGUSD"}


def parse_swissquote(payload: list, symbol: str) -> Tick | None:
    best = None
    for entry in payload or []:
        profiles = entry.get("spreadProfilePrices") or []
        chosen = next((p for p in profiles if p.get("spreadProfile") == "prime"), profiles[0] if profiles else None)
        if not chosen:
            continue
        ts = float(entry.get("ts", 0)) / 1000.0
        tk = Tick(symbol, ts, float(chosen["bid"]), float(chosen["ask"]), source="Swissquote public")
        if best is None or tk.time > best.time:
            best = tk
    return best


def parse_yahoo(payload: dict) -> tuple[pd.DataFrame, dict]:
    res = (payload.get("chart") or {}).get("result") or []
    if not res:
        return empty_bars(), {}
    r = res[0]
    ts = r.get("timestamp") or []
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    rows = []
    for i, t in enumerate(ts):
        o, h, low, c = (q.get(k, [None] * len(ts))[i] for k in ("open", "high", "low", "close"))
        if None in (o, h, low, c):
            continue
        v = (q.get("volume") or [0] * len(ts))[i] or 0
        rows.append({"time": int(t), "open": o, "high": h, "low": low, "close": c, "tick_volume": float(v),
                     "real_volume": 0.0, "spread": 0.0})
    df = normalize(pd.DataFrame(rows)) if rows else empty_bars()
    return df, r.get("meta") or {}


def align_bars(df: pd.DataFrame, sec: int) -> pd.DataFrame:
    """Yahoo appends a partial bar stamped with the last trade time; snap all times to the interval grid
    and merge duplicates."""
    if len(df) == 0:
        return df
    t = (df["time"].to_numpy("int64") // sec) * sec
    g = df.assign(time=t).groupby("time", sort=True)
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last(), "tick_volume": g["tick_volume"].sum(),
                        "real_volume": g["real_volume"].sum(), "spread": g["spread"].max()}).reset_index()
    return normalize(out)


class FallbackPublicAdapter(MarketDataAdapter):
    kind = "fallback"
    is_preview = True

    def __init__(self, client: httpx.Client | None = None, day_boundary: dict | None = None,
                 clock=time.time):
        super().__init__()
        self.client = client or httpx.Client(timeout=12.0, headers={"User-Agent": UA}, follow_redirects=True)
        self.day_boundary = day_boundary
        self.clock = clock
        self.label = "PREVIEW ONLY · not for trading"
        self._tick_cache: dict[str, Tick] = {}
        self._tick_fetch: dict[str, float] = {}
        self._hist: dict[str, deque] = {}
        self._yh_cache: dict[tuple[str, str], tuple[float, pd.DataFrame, dict]] = {}
        self._basis: dict[str, deque] = {}
        self._lock = threading.RLock()
        self.delay_seconds: dict[str, float] = {}
        self.volume_ok: dict[str, bool] = {}

    def connect(self) -> bool:
        try:
            t = self.tick("EURUSD")
            self.connected = t is not None
        except Exception as e:
            self.last_error = str(e)[:200]
            self.connected = False
        return self.connected

    def resolve_symbol(self, canonical_name: str) -> str | None:
        return canonical_name if yahoo_ticker(canonical_name) else None

    def symbol_spec(self, canonical_name: str) -> SymbolSpec | None:
        return generic_spec(canonical_name)

    # ------------------------------------------------------------------ live quotes
    def tick(self, canonical_name: str) -> Tick | None:
        now = self.clock()
        with self._lock:
            if now - self._tick_fetch.get(canonical_name, 0) < 0.9 and canonical_name in self._tick_cache:
                return self._tick_cache[canonical_name]
            self._tick_fetch[canonical_name] = now
        try:
            r = self.client.get(SQ_URL.format(inst=swissquote_instrument(canonical_name)))
            if r.status_code != 200:
                raise RuntimeError(f"Swissquote HTTP {r.status_code}")
            tk = parse_swissquote(r.json(), canonical_name)
        except Exception as e:
            self.last_error = str(e)[:200]
            return self._tick_cache.get(canonical_name)
        if tk:
            with self._lock:
                self._tick_cache[canonical_name] = tk
                self.last_tick_time[canonical_name] = tk.time
                h = self._hist.setdefault(canonical_name, deque(maxlen=20000))
                if not h or h[-1][0] < tk.time:
                    h.append((tk.time, tk.mid))
            self.connected = True
        return tk

    def _spot_at(self, symbol: str, ts: float) -> float | None:
        h = self._hist.get(symbol)
        if not h:
            return None
        best = min(h, key=lambda x: abs(x[0] - ts))
        return best[1] if abs(best[0] - ts) <= 90 else None

    # ------------------------------------------------------------------ history
    def _yahoo(self, symbol: str, tf: str) -> tuple[pd.DataFrame, dict]:
        interval, rng, ttl = YH_PLAN[tf]
        key = (symbol, interval)
        now = self.clock()
        with self._lock:
            hit = self._yh_cache.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1], hit[2]
        ticker = yahoo_ticker(symbol)
        if not ticker:
            return empty_bars(), {}
        try:
            r = self.client.get(YH_URL.format(ticker=ticker), params={"interval": interval, "range": rng})
            if r.status_code != 200:
                raise RuntimeError(f"Yahoo HTTP {r.status_code}")
            df, meta = parse_yahoo(r.json())
            df = align_bars(df, TF_SECONDS[tf])
        except Exception as e:
            self.last_error = str(e)[:200]
            if hit:
                return hit[1], hit[2]
            return empty_bars(), {}
        with self._lock:
            self._yh_cache[key] = (now, df, meta)
        return df, meta

    def basis(self, symbol: str, futures_bars: pd.DataFrame, tf: str) -> float | None:
        """Spot - futures offset. Measured at the last futures bar close using collected spot quotes;
        falls back to the current spot when no matching quote was collected yet."""
        if symbol not in FUTURES_BASED or len(futures_bars) == 0:
            return 0.0
        last = futures_bars.iloc[-1]
        close_t = float(last["time"]) + TF_SECONDS[tf]
        spot = self._spot_at(symbol, close_t)
        if spot is None:
            tk = self.tick(symbol)
            if tk is None:
                return None
            spot = tk.mid
        val = spot - float(last["close"])
        dq = self._basis.setdefault(symbol, deque(maxlen=15))
        dq.append(val)
        return statistics.median(dq)

    def bars(self, canonical_name: str, tf: str, count: int) -> pd.DataFrame:
        if tf in ("H4", "D1", "W1"):
            base, meta = self._yahoo(canonical_name, "H1")
            src_tf = "H1"
            df = resample(base, tf, self.day_boundary) if len(base) else base
        else:
            df, meta = self._yahoo(canonical_name, tf)
            src_tf = tf
        if len(df) == 0:
            return df
        df = df.copy()
        off = self.basis(canonical_name, df if src_tf == tf else self._yahoo(canonical_name, "H1")[0], src_tf)
        if off:
            for c in ("open", "high", "low", "close"):
                df[c] = df[c] + off
        self.volume_ok[canonical_name] = float((df["tick_volume"] > 0).mean()) > 0.5
        last_trade = float(meta.get("regularMarketTime") or (float(df["time"].iloc[-1]) + TF_SECONDS[tf]))
        self.delay_seconds[canonical_name] = max(0.0, self.clock() - last_trade)
        df = self._append_live(canonical_name, df, tf)
        return df.tail(count).reset_index(drop=True)

    def _append_live(self, symbol: str, df: pd.DataFrame, tf: str) -> pd.DataFrame:
        h = self._hist.get(symbol)
        if not h:
            return df
        sec = TF_SECONDS[tf]
        last_t = int(df["time"].iloc[-1])
        pts = [(t, p) for t, p in list(h) if t >= last_t + sec]
        if not pts:
            return df
        rows: dict[int, dict] = {}
        for t, p in pts:
            bt = int(t // sec * sec)
            r = rows.get(bt)
            if r is None:
                rows[bt] = {"time": bt, "open": p, "high": p, "low": p, "close": p, "tick_volume": 0.0,
                            "real_volume": 0.0, "spread": 0.0}
            else:
                r["high"] = max(r["high"], p)
                r["low"] = min(r["low"], p)
                r["close"] = p
        add = pd.DataFrame(sorted(rows.values(), key=lambda r: r["time"]))
        return pd.concat([df, add], ignore_index=True)

    def has_volume(self, canonical_name: str) -> bool:
        if canonical(canonical_name) not in FUTURES_BASED:
            return False
        return self.volume_ok.get(canonical_name, True)

    def preview_label(self, symbol: str) -> str:
        d = self.delay_seconds.get(symbol)
        dtxt = f"{d:.0f}s" if d is not None else "n/a"
        return f"PREVIEW ONLY · not for trading · history delayed {dtxt}"
