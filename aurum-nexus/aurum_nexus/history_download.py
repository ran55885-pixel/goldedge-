"""Download M1 history into the backtest CSV format (docs/BACKTEST_CSV.md).

Sources:
  mt5    - official MetaTrader5 copy_rates_range (Windows; broker prices, tick volume, spread in points).
           Server time is converted to UTC with the account's server_utc_offset_hours.
  oanda  - OANDA v20 candles (M1, mid prices, tick-count volume; spread from bid/ask).
  yahoo  - keyless Yahoo chart API, 1-minute bars for roughly the last 7-30 days. PREVIEW/RESEARCH ONLY:
           gold/silver are COMEX futures (GC=F/SI=F, not spot) and FX has no volume. Files are labelled.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

from aurum_nexus.backtest.csvio import write_m1_csv
from aurum_nexus.data.bars import normalize
from aurum_nexus.data.fallback_adapter import UA, YH_URL, parse_yahoo
from aurum_nexus.data.oanda_adapter import OandaAdapter, parse_candles
from aurum_nexus.symbols import oanda_instrument, yahoo_ticker


def _dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def download_mt5(account_cfg, symbols: list[str], start: float, end: float, out_dir: str | Path, mt5_module=None,
                 suffixes: list[str] | None = None, log=print) -> list[Path]:
    from aurum_nexus.data.mt5_adapter import Mt5Adapter
    ad = Mt5Adapter(account_cfg, mt5_module=mt5_module, suffix_candidates=suffixes or [""])
    if not ad.connect():
        raise RuntimeError(f"MT5 connect failed: {ad.last_error}")
    paths = []
    try:
        for s in symbols:
            name = ad.resolve_symbol(s)
            if not name:
                log(f"{s}: not offered by this broker - skipped")
                continue
            chunks = []
            t = start
            while t < end:
                t2 = min(end, t + 30 * 86400)
                df = ad.bars_range(s, "M1", t, t2)
                if len(df):
                    chunks.append(df)
                log(f"{s}: {_dt(t):%Y-%m-%d} -> {_dt(t2):%Y-%m-%d}: {len(df)} bars")
                t = t2
            if not chunks:
                continue
            df = normalize(pd.concat(chunks))
            p = Path(out_dir) / f"{s}.csv"
            write_m1_csv(df, p, f"source: MT5 {account_cfg.server} {name} (broker prices, UTC, spread in points)")
            paths.append(p)
    finally:
        ad.disconnect()
    return paths


def download_oanda(symbols: list[str], start: float, end: float, out_dir: str | Path, client=None, log=print) -> list[Path]:
    o = OandaAdapter(client=client)
    if not o.configured:
        raise RuntimeError("OANDA_TOKEN / OANDA_ACCOUNT_ID missing in .env")
    paths = []
    for s in symbols:
        chunks = []
        t = start
        while t < end:
            params = {"granularity": "M1", "price": "MBA", "from": _dt(t).strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "count": 5000}
            data = o._get(f"{o.api}/v3/instruments/{oanda_instrument(s)}/candles", params)
            df = parse_candles(data)
            if len(df) == 0:
                t += 5000 * 60
                continue
            pts = 10 ** (2 if s.startswith("XAU") else (3 if s.endswith("JPY") else 5))
            df["spread"] = (df["spread"] * pts).round()
            chunks.append(df[df["time"] < end])
            t = float(df["time"].iloc[-1]) + 60
            log(f"{s}: up to {_dt(t):%Y-%m-%d %H:%M} ({sum(len(c) for c in chunks)} bars)")
        if chunks:
            p = Path(out_dir) / f"{s}.csv"
            write_m1_csv(normalize(pd.concat(chunks)), p, "source: OANDA v20 M1 mid (tick-count volume)")
            paths.append(p)
    return paths


def download_yahoo(symbols: list[str], out_dir: str | Path, days: int = 7, client=None, log=print) -> list[Path]:
    client = client or httpx.Client(timeout=20.0, headers={"User-Agent": UA})
    paths = []
    now = int(time.time())
    for s in symbols:
        tk = yahoo_ticker(s)
        if not tk:
            continue
        chunks = []
        p2 = now
        while p2 > now - days * 86400:
            p1 = max(now - days * 86400, p2 - 7 * 86400)
            r = client.get(YH_URL.format(ticker=tk), params={"interval": "1m", "period1": p1, "period2": p2})
            if r.status_code == 200:
                df, _ = parse_yahoo(r.json())
                if len(df):
                    chunks.append(df)
            else:
                log(f"{s}: Yahoo HTTP {r.status_code} for {_dt(p1):%Y-%m-%d}")
            p2 = p1
            time.sleep(0.5)
        if not chunks:
            log(f"{s}: no data")
            continue
        df = normalize(pd.concat(chunks))
        df = df[(df["time"] % 60) == 0]
        p = Path(out_dir) / f"{s}.csv"
        note = (f"source: Yahoo {tk} 1m - PREVIEW/RESEARCH ONLY (delayed; "
                + ("COMEX futures, not spot" if tk.endswith("=F") else "no volume") + ")")
        write_m1_csv(df, p, note)
        paths.append(p)
        log(f"{s}: {len(df)} bars -> {p}")
    return paths


# ------------------------------------------------------------------ Dukascopy (free bank-grade spot history)
DUKA_URL = "https://datafeed.dukascopy.com/datafeed/{inst}/{y}/{m:02d}/{d:02d}/{side}_candles_min_1.bi5"


def duka_scale(symbol: str) -> float:
    """Dukascopy stores prices as integers: metals and JPY pairs x1000, other FX x100000."""
    s = symbol.upper()
    if s.startswith(("XAU", "XAG")) or s.endswith("JPY"):
        return 1000.0
    return 100000.0


def parse_duka_candles(raw: bytes, day_start: int, scale: float) -> pd.DataFrame:
    """Decode one day of Dukascopy M1 candles (LZMA; records: int32 secs, open, close, low, high, float32 volume)."""
    import lzma
    import struct
    if not raw:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "tick_volume"])
    data = lzma.decompress(raw)
    rows = [struct.unpack(">5if", data[i:i + 24]) for i in range(0, len(data) - 23, 24)]
    df = pd.DataFrame(rows, columns=["t", "open", "close", "low", "high", "tick_volume"])
    df["time"] = day_start + df["t"].astype("int64")
    for c in ("open", "close", "low", "high"):
        df[c] = df[c] / scale
    return df[["time", "open", "high", "low", "close", "tick_volume"]]


def download_dukascopy(symbols: list[str], start: float, end: float, out_dir: str | Path, client=None,
                       log=print, workers: int = 3) -> list[Path]:
    """Bid M1 candles + ask closes from Dukascopy (Swiss bank). Spread (points) = ask - bid at the close.
    Volume is Dukascopy's own traded volume - a tick-volume style activity measure, not exchange volume."""
    from concurrent.futures import ThreadPoolExecutor
    client = client or httpx.Client(timeout=30.0, headers={"User-Agent": UA})
    paths = []
    day0 = int(start // 86400 * 86400)
    days = [d for d in range(day0, int(end), 86400) if datetime.fromtimestamp(d, tz=timezone.utc).weekday() != 5]

    def fetch(sym: str, day: int, side: str) -> bytes:
        dt = _dt(day)
        url = DUKA_URL.format(inst=sym, y=dt.year, m=dt.month - 1, d=dt.day, side=side)   # month is 0-based
        for attempt in range(8):
            try:
                r = client.get(url)
                if r.status_code == 200:
                    return r.content
                if r.status_code == 404:
                    return b""
                if r.status_code == 429:                  # rate limited: back off harder
                    time.sleep(10 * (attempt + 1))
                    continue
            except httpx.HTTPError:
                pass
            time.sleep(2 * (attempt + 1))
        log(f"{sym} {dt:%Y-%m-%d} {side}: failed after retries")
        return b""

    for s in symbols:
        scale = duka_scale(s)
        digits = 2 if s.startswith(("XAU", "XAG")) else (3 if s.endswith("JPY") else 5)
        chunks = []
        with ThreadPoolExecutor(workers) as ex:
            bids = list(ex.map(lambda d: fetch(s, d, "BID"), days))
            asks = list(ex.map(lambda d: fetch(s, d, "ASK"), days))
        for day, b, a in zip(days, bids, asks):
            bid = parse_duka_candles(b, day, scale)
            if not len(bid):
                continue
            ask = parse_duka_candles(a, day, scale)[["time", "close"]].rename(columns={"close": "ask_close"})
            df = bid.merge(ask, on="time", how="left")
            df["spread"] = ((df["ask_close"] - df["close"]) * 10 ** digits).round().clip(lower=0).fillna(0)
            df["real_volume"] = 0.0
            # Dukascopy writes flat zero-volume candles when the market is closed - drop them
            df = df[(df["tick_volume"] > 0) | (df["high"] != df["low"])]
            chunks.append(df.drop(columns=["ask_close"]))
        if not chunks:
            log(f"{s}: no Dukascopy data")
            continue
        df = normalize(pd.concat(chunks))
        df = df[(df["time"] >= start) & (df["time"] < end)]
        p = Path(out_dir) / f"{s}.csv"
        write_m1_csv(df, p, "source: Dukascopy Bank SA M1 bid + ask-bid spread (UTC, spread in points, "
                            "Dukascopy volume)")
        paths.append(p)
        log(f"{s}: {len(df)} bars {_dt(df['time'].iloc[0]):%Y-%m-%d} -> {_dt(df['time'].iloc[-1]):%Y-%m-%d} -> {p}")
    return paths
