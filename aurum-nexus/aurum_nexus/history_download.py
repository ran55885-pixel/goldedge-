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
