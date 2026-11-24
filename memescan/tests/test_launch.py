import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

from memescan.launch import LaunchTracker  # noqa: E402
from memescan.sources import Pair  # noqa: E402

CFG = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..", "config.yaml")))["launch"]


def create(mint="M", dev_tokens=20_000_000, mcap_sol=28.0):
    return {"txType": "create", "mint": mint, "symbol": "NEW", "name": "New", "traderPublicKey": "DEV",
            "initialBuy": dev_tokens, "marketCapSol": mcap_sol}


def pair(mcap_sol=140.0, sol_usd=150.0, **kw):
    price_native = mcap_sol / 1e9
    base = dict(chain="solana", token="M", symbol="NEW", name="New", pair_address="P", dex="pumpfun", url="u",
                price_usd=price_native * sol_usd, liquidity_usd=0, fdv_usd=mcap_sol * sol_usd, age_minutes=3,
                volume={"m5": 15_000}, price_change={"m5": 60},
                txns={"m5": {"buys": 120, "sells": 50}}, price_native=price_native, quote_symbol="SOL")
    base.update(kw)
    return Pair(**base)


class TestLaunch(unittest.TestCase):
    def test_runner_signals(self):
        t = LaunchTracker(CFG)
        t.on_message(create(), now=0)
        self.assertEqual(t.ready(CFG["min_age_seconds"] - 1), [])        # too young
        self.assertEqual(t.ready(CFG["min_age_seconds"] + 1), ["M"])
        fails, score, _ = t.evaluate(t.watch["M"], pair())
        self.assertEqual(fails, [])
        self.assertAlmostEqual(t.multiple(t.watch["M"], pair()), 5.0)
        self.assertGreaterEqual(score, CFG["entry_min_score"])

    def test_confirmation_needs_consecutive_passes(self):
        t = LaunchTracker(CFG)
        t.on_message(create(), now=0)
        launch = t.watch["M"]
        self.assertFalse(t.confirm(launch, True))
        self.assertFalse(t.confirm(launch, False))   # streak broken
        self.assertFalse(t.confirm(launch, True))
        self.assertTrue(t.confirm(launch, True))

    def test_bad_data_gives_no_multiple(self):
        t = LaunchTracker(CFG)
        t.on_message(create(mcap_sol=0.001), now=0)   # garbage launch mcap (the x391,280,180 bug)
        self.assertEqual(t.multiple(t.watch["M"], pair()), 0.0)
        t.on_message(create(mint="N"), now=0)
        self.assertEqual(t.multiple(t.watch["N"], pair(quote_symbol="USDC")), 0.0)

    def test_fat_dev_rejected(self):
        t = LaunchTracker(CFG)
        t.on_message(create(dev_tokens=150_000_000), now=0)   # 15% of supply
        fails, _, _ = t.evaluate(t.watch["M"], pair())
        self.assertTrue(any("dev holds" in f for f in fails))

    def test_flat_coin_rejected(self):
        t = LaunchTracker(CFG)
        t.on_message(create(), now=0)
        fails, _, _ = t.evaluate(t.watch["M"], pair(mcap_sol=35, price_change={"m5": 2}))
        self.assertTrue(any(f.startswith("only x") for f in fails))
        self.assertIn("not climbing now", fails)

    def test_trade_stream_dev_sell_and_buyers(self):
        t = LaunchTracker(CFG, has_trades=True)
        t.on_message(create(), now=0)
        for i in range(10):
            t.on_message({"txType": "buy", "mint": "M", "traderPublicKey": f"W{i}"})
        t.on_message({"txType": "sell", "mint": "M", "traderPublicKey": "DEV"})
        fails, _, _ = t.evaluate(t.watch["M"], pair())
        self.assertIn("few unique buyers", fails)
        self.assertIn("dev sold", fails)

    def test_migration_and_expiry(self):
        t = LaunchTracker(CFG)
        t.on_message(create(), now=0)
        t.on_message({"txType": "migrate", "mint": "M"})
        self.assertTrue(t.watch["M"].migrated)
        t.expire(CFG["watch_minutes"] * 60 + 1, keep={"M"})
        self.assertIn("M", t.watch)                 # kept while we hold a position
        t.expire(CFG["watch_minutes"] * 60 + 1)
        self.assertNotIn("M", t.watch)


if __name__ == "__main__":
    unittest.main()
