import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

from memescan.intel import sources as S  # noqa: E402
from memescan.intel.engine import Intel  # noqa: E402
from memescan.intel.extract import cashtags, evm_addresses, solana_addresses, topics  # noqa: E402
from memescan.intel.store import Store  # noqa: E402

CFG = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..", "config.yaml")))
MINT = "B34nXKzCgsJb9ChLuSHPRpXLE3WFSWyeT8Cqkzzmpump"
EVM = "0x29CC30F9D113B356CE408667AA6433589CECBDCA"


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)


class FakeFetcher:
    def __init__(self, markets):
        self.markets, self.errors = markets, {}

    def exchange_markets(self):
        return self.markets


class TestExtract(unittest.TestCase):
    def test_addresses(self):
        text = f"aping {MINT} now!! also {EVM} and wSOL So11111111111111111111111111111111111111112"
        self.assertEqual(solana_addresses(text), {MINT})
        self.assertEqual(evm_addresses(text), {EVM.lower()})

    def test_plain_words_are_not_addresses(self):
        self.assertEqual(solana_addresses("supercalifragilisticexpialidociousword thing"), set())

    def test_cashtags_and_topics(self):
        self.assertEqual(cashtags("buy $wif and $BONK, not 5$"), {"WIF", "BONK"})
        self.assertIn("listing", topics("Binance Will List Dogwifhat (WIF)"))
        self.assertIn("hack", topics("DEX drained in $40M exploit"))


class TestParsers(unittest.TestCase):
    def test_rss_and_atom(self):
        rss = """<rss><channel><item><title>Upbit lists PENGU</title><link>https://x/1</link>
        <description>&lt;p&gt;Korean exchange&lt;/p&gt;</description>
        <pubDate>Mon, 23 Nov 2026 20:00:00 GMT</pubDate></item></channel></rss>"""
        items = S.parse_feed("CoinDesk", rss)
        self.assertEqual(items[0].title, "Upbit lists PENGU")
        self.assertEqual(items[0].text.strip(), "Korean exchange")
        self.assertEqual(items[0].tier, "news")
        atom = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>T</title>
        <link href="https://y/2"/><summary>S</summary></entry></feed>"""
        self.assertEqual(S.parse_feed("X", atom)[0].url, "https://y/2")

    def test_exchange_parsers(self):
        self.assertEqual(S.parse_binance_symbols({"symbols": [{"baseAsset": "WIF", "status": "TRADING"},
                                                              {"baseAsset": "OLD", "status": "BREAK"}]}), {"WIF"})
        self.assertEqual(S.parse_upbit_markets([{"market": "KRW-PENGU"}, {"market": "BTC-PENGU"}]), {"PENGU"})
        self.assertEqual(S.parse_bithumb({"data": {"BTC": {}, "date": "1"}}), {"BTC"})
        self.assertEqual(S.parse_coinbase_products([{"base_currency": "BONK", "status": "online"}]), {"BONK"})

    def test_community_parsers(self):
        chan = [{"threads": [{"no": 1, "sub": "SOL gem", "com": f"CA: {MINT}", "time": 5,
                              "last_replies": [{"no": 2, "com": "rug lol", "time": 6}]}]}]
        items = S.parse_4chan_catalog(chan)
        self.assertEqual(len(items), 2)
        self.assertIn(MINT, items[0].text)
        self.assertEqual(items[0].group, items[1].group)  # same thread = not independent
        tg = ('<div data-post="chan/77"><div class="tgme_widget_message_text js-message_text" dir="auto">'
              f'New: <b>{MINT}</b></div></div>')
        t = S.parse_telegram_public("chan", tg)
        self.assertEqual(t[0].url, "https://t.me/chan/77")
        self.assertIn(MINT, t[0].text)
        rd = S.parse_reddit("solana", {"data": {"children": [{"data": {
            "id": "a1", "title": "gem", "selftext": MINT, "author": "u1", "permalink": "/r/x", "created_utc": 1}}]}})
        self.assertEqual(rd[0].group, "reddit:u1")


class TestStoreAndEngine(unittest.TestCase):
    def setUp(self):
        self.store = Store(os.path.join(tempfile.mkdtemp(), "intel.db"))
        self.note = FakeNotifier()
        self.intel = Intel(CFG, self.store, FakeFetcher({}), self.note)
        self.intel.resolve = lambda addrs: {a: {"chain": "solana", "symbol": "FIONA", "price": 1.0,
                                                "url": "u", "liq": 1} for a in addrs}

    def item(self, src, group, key, ts=None):
        return S.Item(src, group, "community", key, "gem", f"CA {MINT}", "url", time.time() if ts is None else ts)

    def test_multi_source_alert_needs_independent_groups(self):
        self.intel.first_pass = {"a", "b"}  # past warmup
        self.intel.process([self.item("reddit:solana", "reddit:u1", "k1")], "a")
        self.intel.process([self.item("reddit:memecoins", "reddit:u1", "k2")], "a")   # same author
        self.assertEqual(self.note.sent, [])
        self.intel.process([self.item("4chan:biz", "4chan:99", "k3")], "b")
        self.assertEqual(len(self.note.sent), 1)
        self.assertIn("MULTI-SOURCE x2", self.note.sent[0])
        self.assertEqual(self.store.buzz_count(MINT, 60), 3)

    def test_warmup_and_stale_items_are_not_calls(self):
        self.intel.process([self.item("reddit:solana", "reddit:u1", "k1")], "a")          # warmup pass
        self.intel.process([self.item("4chan:biz", "4chan:1", "k2", ts=time.time() - 7200)], "a")  # 2h old
        self.assertEqual(self.store.buzz_count(MINT, 600), 0)

    def test_dedupe(self):
        self.intel.first_pass = {"a"}
        it = self.item("reddit:solana", "reddit:u1", "k1")
        self.intel.process([it], "a")
        self.intel.process([it], "a")
        self.assertEqual(self.store.buzz_count(MINT, 60), 1)

    def test_listing_diff(self):
        self.intel.fetch = FakeFetcher({"Upbit": {"BTC", "ETH"}})
        self.intel.check_listings()                        # baseline
        self.assertEqual(self.note.sent, [])
        self.intel.fetch = FakeFetcher({"Upbit": {"BTC", "ETH", "PENGU"}})
        self.intel.check_listings()
        self.assertEqual(len(self.note.sent), 1)
        self.assertIn("PENGU", self.note.sent[0])
        self.intel.check_listings()                        # no repeat
        self.assertEqual(len(self.note.sent), 1)

    def test_reliability_learning(self):
        now = time.time()
        for i, (src, later) in enumerate([("good", 3.0), ("good", 2.0), ("good", 0.9), ("bad", 0.05), ("bad", 0.1)]):
            self.store.add_mention(src, src, "solana", f"T{i}", "X", 1.0, "u", now - 7200)
        self.intel.resolve = lambda addrs: {a: {"price": {"T0": 3.0, "T1": 2.0, "T2": 0.9, "T3": 0.05,
                                                          "T4": 0.1}[a]} for a in addrs}
        self.intel.update_outcomes(now)
        stats = {s["source"]: s for s in self.store.source_stats()}
        self.assertEqual(stats["good"]["hits"], 2)
        self.assertEqual(stats["bad"]["rugs"], 2)
        self.assertGreater(stats["good"]["edge"], stats["bad"]["edge"])
        self.assertEqual(self.store.pending_outcomes(now), [])   # 1h done, 24h not due yet


if __name__ == "__main__":
    unittest.main()
