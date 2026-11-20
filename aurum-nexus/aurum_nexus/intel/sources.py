"""Slow-moving context: CFTC COT (gold managed money), FRED real & nominal 10Y yields, OANDA sentiment,
MT5 Depth of Market. Each returns None/'n/a' when unavailable - never raises into the trading cycle."""
from __future__ import annotations

import csv
import io
import os
import time

import httpx
import numpy as np

COT_URL = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"   # Disaggregated Futures Only
GOLD_COT_CODE = "088691"                                                 # GOLD - COMMODITY EXCHANGE INC.
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
FRED_API = "https://api.stlouisfed.org/fred/series/observations"


def _client(client):
    return client or httpx.Client(timeout=20.0, headers={"User-Agent": "AurumNexus/1.0"})


def parse_cot(rows: list[dict]) -> dict | None:
    pts = []
    for r in rows or []:
        try:
            net = float(r["m_money_positions_long_all"]) - float(r["m_money_positions_short_all"])
            pts.append((r["report_date_as_yyyy_mm_dd"][:10], net, float(r.get("open_interest_all") or 0)))
        except (KeyError, ValueError, TypeError):
            continue
    if len(pts) < 10:
        return None
    pts.sort()
    nets = np.array([p[1] for p in pts])
    last = nets[-1]
    pct = float((nets[:-1] < last).mean() * 100)
    return {"date": pts[-1][0], "net": float(last), "change": float(last - nets[-2]), "percentile": round(pct, 1),
            "weeks": len(pts), "open_interest": pts[-1][2], "source": "CFTC disaggregated (managed money)"}


def fetch_cot(client=None) -> dict | None:
    params = {"$where": f"cftc_contract_market_code='{GOLD_COT_CODE}'", "$order": "report_date_as_yyyy_mm_dd DESC",
              "$limit": "156", "$select": "report_date_as_yyyy_mm_dd,m_money_positions_long_all,"
                                          "m_money_positions_short_all,open_interest_all"}
    r = _client(client).get(COT_URL, params=params)
    if r.status_code != 200:
        raise RuntimeError(f"COT HTTP {r.status_code}")
    return parse_cot(r.json())


def parse_fred_csv(text: str) -> list[tuple[str, float]]:
    out = []
    rd = csv.reader(io.StringIO(text))
    next(rd, None)
    for row in rd:
        if len(row) < 2:
            continue
        try:
            out.append((row[0], float(row[1])))
        except ValueError:
            continue  # FRED uses '.' for missing
    return out


def fetch_fred(series: str, client=None) -> list[tuple[str, float]]:
    key = os.environ.get("FRED_API_KEY")
    c = _client(client)
    if key:
        r = c.get(FRED_API, params={"series_id": series, "api_key": key, "file_type": "json",
                                    "sort_order": "desc", "limit": 120})
        if r.status_code == 200:
            obs = r.json().get("observations", [])
            vals = []
            for o in obs:
                try:
                    vals.append((o["date"], float(o["value"])))
                except (KeyError, ValueError):
                    pass
            return sorted(vals)
    r = c.get(FRED_CSV.format(sid=series))
    if r.status_code != 200:
        raise RuntimeError(f"FRED HTTP {r.status_code}")
    return parse_fred_csv(r.text)[-400:]


def yield_trend(obs: list[tuple[str, float]], lookback: int = 20, threshold: float = 0.10) -> dict | None:
    if len(obs) < lookback + 1:
        return None
    last = obs[-1][1]
    prev = obs[-1 - lookback][1]
    ch = last - prev
    trend = "rising" if ch > threshold else ("falling" if ch < -threshold else "flat")
    return {"date": obs[-1][0], "value": last, "change": round(ch, 3), "trend": trend, "lookback_obs": lookback}


def dom_imbalance(book: list[dict] | None, price: float, band: float) -> dict | None:
    if not book or not price or band <= 0:
        return None
    near = [b for b in book if abs(b["price"] - price) <= band]
    if not near:
        return None
    bid = sum(b["volume"] for b in near if b["side"] == "buy")
    ask = sum(b["volume"] for b in near if b["side"] == "sell")
    tot = bid + ask
    if tot <= 0:
        return None
    vols = np.array([b["volume"] for b in book])
    med = float(np.median(vols)) if len(vols) else 0
    walls = [b for b in book if med > 0 and b["volume"] >= 3 * med]
    return {"imbalance": round((bid - ask) / tot, 3), "bid_volume": bid, "ask_volume": ask,
            "walls": sorted(walls, key=lambda b: -b["volume"])[:4]}


class IntelHub:
    """Caches all optional sources; refresh() is called from a background thread by the engine."""

    def __init__(self, cfg, oanda=None, client=None, clock=time.time):
        self.cfg = cfg
        self.ic = cfg.get("intel")
        self.oanda = oanda
        self.client = client
        self.clock = clock
        self.cot: dict | None = None
        self.real_yield: dict | None = None
        self.nominal_yield: dict | None = None
        self.sentiment: dict[str, dict] = {}
        self.dom: dict[str, dict] = {}
        self.errors: dict[str, str] = {}
        self._t: dict[str, float] = {}

    def _due(self, key: str, seconds: float) -> bool:
        return self.clock() - self._t.get(key, 0) >= seconds

    def refresh(self, symbols: list[str]) -> None:
        if self.ic["cot"]["enabled"] and self._due("cot", 3600 * self.ic["cot"]["refresh_hours"]):
            self._t["cot"] = self.clock()
            try:
                self.cot = fetch_cot(self.client)
                self.errors.pop("cot", None)
            except Exception as e:
                self.errors["cot"] = str(e)[:150]
        if self.ic["macro"]["enabled"] and self._due("macro", 3600 * self.ic["macro"]["refresh_hours"]):
            self._t["macro"] = self.clock()
            try:
                self.real_yield = yield_trend(fetch_fred("DFII10", self.client))
                self.nominal_yield = yield_trend(fetch_fred("DGS10", self.client))
                self.errors.pop("macro", None)
            except Exception as e:
                self.errors["macro"] = str(e)[:150]
        if self.ic["sentiment"]["enabled"] and self.oanda is not None and getattr(self.oanda, "connected", False) \
                and self._due("sentiment", 60 * self.ic["sentiment"]["refresh_minutes"]):
            self._t["sentiment"] = self.clock()
            for s in symbols:
                pb = self.oanda.position_book(s)
                if pb:
                    self.sentiment[s] = pb

    def update_dom(self, symbol: str, book, price: float | None, atr: float | None) -> None:
        if not self.ic["dom"]["enabled"] or not price or not atr:
            return
        d = dom_imbalance(book, price, float(self.ic["dom"]["levels_atr_mult"]) * atr)
        if d:
            self.dom[symbol] = {**d, "time": self.clock()}

    def score_modifier(self, symbol: str, direction: str) -> dict:
        pts = 0.0
        notes = []
        sign = 1 if direction == "long" else -1
        if symbol in ("XAUUSD", "XAGUSD"):
            ry = self.real_yield
            if ry:
                if ry["trend"] == "rising":
                    pts += -3 if sign > 0 else 2
                    notes.append(f"real yield rising ({ry['change']:+.2f})")
                elif ry["trend"] == "falling":
                    pts += 3 if sign > 0 else -2
                    notes.append(f"real yield falling ({ry['change']:+.2f})")
            if symbol == "XAUUSD" and self.cot:
                p = self.cot["percentile"]
                if p >= 90:
                    pts += -2 * sign
                    notes.append(f"COT crowded long ({p:.0f} pct)")
                elif p <= 10:
                    pts += 2 * sign
                    notes.append(f"COT washed out ({p:.0f} pct)")
        se = self.sentiment.get(symbol)
        if se:
            if se["long_pct"] >= 70:
                pts += -1.5 * sign
                notes.append(f"crowd {se['long_pct']:.0f}% long (contrarian)")
            elif se["long_pct"] <= 30:
                pts += 1.5 * sign
                notes.append(f"crowd {se['long_pct']:.0f}% long (contrarian)")
        dm = self.dom.get(symbol)
        if dm and self.clock() - dm["time"] < 30 and abs(dm["imbalance"]) >= 0.3:
            if np.sign(dm["imbalance"]) == sign:
                pts += 1
                notes.append(f"DOM imbalance {dm['imbalance']:+.2f}")
        return {"points": max(-5.0, min(5.0, pts)), "notes": notes}

    def snapshot(self) -> dict:
        return {"cot": self.cot or "n/a", "real_yield": self.real_yield or "n/a",
                "nominal_yield": self.nominal_yield or "n/a", "sentiment": self.sentiment or "n/a",
                "dom": self.dom or "n/a", "errors": self.errors}
