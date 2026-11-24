"""Intel sources. Each one is fetch (network) + parse (pure, testable).

Trust tiers say how much we believe an item is TRUE:
  exchange   official exchange APIs / announcements       1.0
  news       established crypto newsrooms (RSS)          0.85
  aggregator CoinGecko trending, CryptoPanic              0.6
  community  Reddit, 4chan /biz/, public Telegram          0.3
A community mention is never treated as fact; it is an early signal whose predictive value
is measured per source over time (see Store.source_stats).
"""
from __future__ import annotations

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime

import httpx

from .extract import clean

UA = {"User-Agent": "Mozilla/5.0 (memescan research bot)", "Accept": "*/*"}


@dataclass
class Item:
    source: str              # e.g. "rss:CoinDesk", "reddit:solana", "tg:channel"
    group: str               # independence group: same group = not independent
    tier: str                # exchange | news | aggregator | community
    key: str                 # unique id for dedupe
    title: str
    text: str = ""
    url: str = ""
    ts: float = field(default_factory=time.time)
    symbols: list = field(default_factory=list)  # for listings / trending


# ---------------------------------------------------------------- news (RSS / Atom)
def parse_feed(name: str, xml_text: str) -> list[Item]:
    root = ET.fromstring(xml_text)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        desc = clean(it.findtext("description") or "")
        ts = time.time()
        if it.findtext("pubDate"):
            try:
                ts = parsedate_to_datetime(it.findtext("pubDate")).timestamp()
            except (TypeError, ValueError):
                pass
        out.append(Item(f"rss:{name}", f"rss:{name}", "news", f"rss:{link or title}", title, desc, link, ts))
    for it in root.findall("a:entry", ns):
        title = (it.findtext("a:title", default="", namespaces=ns) or "").strip()
        link_el = it.find("a:link", ns)
        link = link_el.get("href", "") if link_el is not None else ""
        summ = clean(it.findtext("a:summary", default="", namespaces=ns))
        out.append(Item(f"rss:{name}", f"rss:{name}", "news", f"rss:{link or title}", title, summ, link))
    return out


# ---------------------------------------------------------------- exchange listings (diff of market lists)
def parse_binance_symbols(data: dict) -> set[str]:
    return {s["baseAsset"] for s in data.get("symbols", []) if s.get("status") == "TRADING"}


def parse_coinbase_products(data: list) -> set[str]:
    return {p["base_currency"] for p in data if not p.get("trading_disabled") and p.get("status") == "online"}


def parse_upbit_markets(data: list) -> set[str]:
    return {m["market"].split("-", 1)[1] for m in data if "-" in m.get("market", "")}


def parse_bithumb(data: dict) -> set[str]:
    return {k for k in (data.get("data") or {}) if k != "date"}


EXCHANGES = {
    "Binance": ("https://api.binance.com/api/v3/exchangeInfo", parse_binance_symbols,
                "https://www.binance.com/en/trade/{}_USDT"),
    "Coinbase": ("https://api.exchange.coinbase.com/products", parse_coinbase_products,
                 "https://www.coinbase.com/price/{}"),
    "Upbit": ("https://api.upbit.com/v1/market/all", parse_upbit_markets, "https://upbit.com/exchange?code=CRIX.UPBIT.KRW-{}"),
    "Bithumb": ("https://api.bithumb.com/public/ticker/ALL_KRW", parse_bithumb, "https://www.bithumb.com/react/trade/order/{}-KRW"),
}


def parse_binance_announcements(data: dict) -> list[Item]:
    out = []
    for cat in (data.get("data") or {}).get("catalogs", []):
        for a in cat.get("articles", []):
            url = f"https://www.binance.com/en/support/announcement/{a.get('code', '')}"
            out.append(Item("binance:announcements", "exchange:Binance", "exchange", f"bnann:{a.get('id')}",
                            a.get("title", ""), "", url, (a.get("releaseDate") or time.time() * 1000) / 1000))
    return out


# ---------------------------------------------------------------- aggregators
def parse_coingecko_trending(data: dict) -> list[Item]:
    out = []
    for c in data.get("coins", []):
        it = c.get("item", {})
        sym = (it.get("symbol") or "").upper()
        out.append(Item("coingecko:trending", "coingecko", "aggregator",
                        f"cgtrend:{it.get('id')}:{time.strftime('%Y-%m-%d')}",
                        f"Trending on CoinGecko: {it.get('name')} ({sym}) rank #{it.get('market_cap_rank')}",
                        "", f"https://www.coingecko.com/en/coins/{it.get('id')}", symbols=[sym]))
    return out


def parse_cryptopanic(data: dict) -> list[Item]:
    out = []
    for p in data.get("results", []):
        src = (p.get("source") or {}).get("title", "cryptopanic")
        out.append(Item("cryptopanic", f"cryptopanic:{src}", "aggregator", f"cp:{p.get('id')}",
                        p.get("title", ""), p.get("description", "") or "", p.get("url") or "",
                        symbols=[c.get("code", "") for c in p.get("currencies") or []]))
    return out


# ---------------------------------------------------------------- community
def parse_reddit(sub: str, data: dict) -> list[Item]:
    out = []
    for ch in (data.get("data") or {}).get("children", []):
        d = ch.get("data", {})
        out.append(Item(f"reddit:{sub}", f"reddit:{d.get('author', '?')}", "community", f"rd:{d.get('id')}",
                        d.get("title", ""), d.get("selftext", "") or "",
                        "https://www.reddit.com" + d.get("permalink", ""), float(d.get("created_utc") or time.time())))
    return out


def parse_4chan_catalog(data: list) -> list[Item]:
    out = []
    for page in data:
        for t in page.get("threads", []):
            posts = [t] + list(t.get("last_replies") or [])
            for p in posts:
                text = clean((p.get("sub") or "") + " " + (p.get("com") or ""))
                out.append(Item("4chan:biz", f"4chan:{t['no']}", "community", f"4c:{p['no']}",
                                clean(t.get("sub") or "")[:80] or text[:80], text,
                                f"https://boards.4chan.org/biz/thread/{t['no']}#p{p['no']}", float(p.get("time") or 0)))
    return out


TG_MSG_RE = re.compile(r'data-post="([^"]+)".*?<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)


def parse_telegram_public(channel: str, page_html: str) -> list[Item]:
    out = []
    for post_id, body in TG_MSG_RE.findall(page_html):
        text = clean(body)
        out.append(Item(f"tg:{channel}", f"tg:{channel}", "community", f"tg:{post_id}",
                        text[:100], text, f"https://t.me/{post_id}"))
    return out


# ---------------------------------------------------------------- fetch layer
class Fetcher:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.http = httpx.Client(timeout=20, headers=UA, follow_redirects=True)
        self.errors: dict[str, str] = {}

    def _get(self, url: str, **kw):
        r = self.http.get(url, **kw)
        r.raise_for_status()
        return r

    def safe(self, name: str, fn):
        try:
            res = fn()
            self.errors.pop(name, None)
            return res
        except Exception as e:  # noqa: BLE001 - one broken source must not stop the others
            self.errors[name] = f"{type(e).__name__}: {str(e)[:80]}"
            return []

    def news(self) -> list[Item]:
        out = []
        for name, url in (self.cfg.get("news_rss", {}).get("feeds") or {}).items():
            out += self.safe(f"rss:{name}", lambda u=url, n=name: parse_feed(n, self._get(u).text))
        return out

    def exchange_markets(self) -> dict[str, set[str]]:
        res = {}
        for name, (url, parser, _) in EXCHANGES.items():
            got = self.safe(f"ex:{name}", lambda u=url, p=parser: p(self._get(u).json()))
            if got:
                res[name] = got
        return res

    def binance_announcements(self) -> list[Item]:
        url = ("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
               "?type=1&pageNo=1&pageSize=20&catalogId=48")
        return self.safe("binance:announcements", lambda: parse_binance_announcements(self._get(url).json()))

    def coingecko_trending(self) -> list[Item]:
        return self.safe("coingecko", lambda: parse_coingecko_trending(
            self._get("https://api.coingecko.com/api/v3/search/trending").json()))

    def cryptopanic(self) -> list[Item]:
        key = os.environ.get("MEMESCAN_CRYPTOPANIC_KEY")
        if not key:
            return []
        url = "https://cryptopanic.com/api/developer/v2/posts/"
        return self.safe("cryptopanic", lambda: parse_cryptopanic(
            self._get(url, params={"auth_token": key, "public": "true"}).json()))

    def reddit(self) -> list[Item]:
        out = []
        for sub in self.cfg.get("reddit", {}).get("subreddits", []):
            out += self.safe(f"reddit:{sub}", lambda s=sub: parse_reddit(
                s, self._get(f"https://www.reddit.com/r/{s}/new.json", params={"limit": 50}).json()))
        return out

    def fourchan(self) -> list[Item]:
        return self.safe("4chan:biz", lambda: parse_4chan_catalog(self._get("https://a.4cdn.org/biz/catalog.json").json()))

    def telegram(self) -> list[Item]:
        out = []
        for ch in self.cfg.get("telegram_public", {}).get("channels", []):
            out += self.safe(f"tg:{ch}", lambda c=ch: parse_telegram_public(c, self._get(f"https://t.me/s/{c}").text))
        return out


def dump(items: list[Item]) -> str:  # debug helper
    return json.dumps([i.__dict__ for i in items], ensure_ascii=False, indent=1, default=list)
