import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

from memescan.paper import PaperTrader  # noqa: E402
from memescan.safety import evaluate_goplus, evaluate_rugcheck  # noqa: E402
from memescan.scoring import hard_filter, score  # noqa: E402
from memescan.sources import Pair  # noqa: E402

CFG = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..", "config.yaml")))


def pair(**kw):
    base = dict(chain="solana", token="TOK", symbol="TOK", name="Tok", pair_address="P", dex="raydium",
                url="u", price_usd=1.0, liquidity_usd=50_000, fdv_usd=400_000, age_minutes=120,
                volume={"m5": 8_000, "h1": 90_000, "h6": 200_000, "h24": 300_000},
                price_change={"m5": 6, "h1": 80, "h6": 150, "h24": 150},
                txns={"m5": {"buys": 60, "sells": 30}, "h1": {"buys": 700, "sells": 400}})
    base.update(kw)
    return Pair(**base)


class TestScoring(unittest.TestCase):
    def test_good_pair_passes_and_scores_high(self):
        p = pair()
        self.assertEqual(hard_filter(p, CFG["filters"]), [])
        self.assertGreaterEqual(score(p)[0], CFG["entry_min_score"])

    def test_falling_pair_rejected(self):
        p = pair(price_change={"m5": -15, "h1": -20, "h6": -60})
        fails = hard_filter(p, CFG["filters"])
        self.assertIn("no upward momentum", fails)
        self.assertIn("dumping now", fails)

    def test_thin_and_too_new(self):
        fails = hard_filter(pair(liquidity_usd=2_000, age_minutes=2), CFG["filters"])
        self.assertIn("low liquidity", fails)
        self.assertIn("too new", fails)


class TestSafety(unittest.TestCase):
    def test_rugcheck(self):
        self.assertTrue(evaluate_rugcheck({"score_normalised": 3, "risks": []}, CFG["safety"]).ok)
        bad = evaluate_rugcheck({"score_normalised": 3, "risks": [{"name": "Mint Authority still enabled",
                                                                  "level": "danger"}]}, CFG["safety"])
        self.assertFalse(bad.ok)
        self.assertFalse(evaluate_rugcheck({}, CFG["safety"]).ok)

    def test_goplus(self):
        clean = {"is_open_source": "1", "buy_tax": "0", "sell_tax": "0.01"}
        self.assertTrue(evaluate_goplus(clean, CFG["safety"]).ok)
        self.assertFalse(evaluate_goplus({**clean, "is_honeypot": "1"}, CFG["safety"]).ok)
        self.assertFalse(evaluate_goplus({**clean, "sell_tax": "0.25"}, CFG["safety"]).ok)
        self.assertFalse(evaluate_goplus({}, CFG["safety"]).ok)


class TestPaper(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.t = PaperTrader(CFG["paper"], self.dir)
        self.p = pair()

    def test_stop_loss(self):
        pos = self.t.enter(self.p, 70, now=0)
        ev = self.t.update("TOK", pos.entry_price * 0.65, 50_000, now=60)
        self.assertEqual(ev[0]["action"], "EXIT stop loss")
        self.assertNotIn("TOK", self.t.positions)
        self.assertLess(float(ev[0]["final_pnl_usd"]), 0)

    def test_take_profit_then_trailing(self):
        pos = self.t.enter(self.p, 70, now=0)
        e = pos.entry_price
        ev = self.t.update("TOK", e * 2.1, 50_000, now=60)
        self.assertEqual(ev[0]["action"], "TAKE PROFIT")
        self.assertIn("TOK", self.t.positions)
        self.t.update("TOK", e * 5, 50_000, now=120)                 # runs to 5x
        ev = self.t.update("TOK", e * 5 * 0.6, 50_000, now=180)      # -40% from peak
        self.assertEqual(ev[0]["action"], "EXIT trailing stop")
        self.assertGreater(float(ev[0]["final_pnl_pct"]), 100)

    def test_rug_exit(self):
        pos = self.t.enter(self.p, 70, now=0)
        ev = self.t.update("TOK", pos.entry_price, 10_000, now=60)
        self.assertEqual(ev[0]["action"], "EXIT rug (liquidity pulled)")

    def test_no_reentry_and_persistence(self):
        self.t.enter(self.p, 70, now=0)
        self.assertEqual(self.t.can_enter("TOK"), "already traded")
        again = PaperTrader(CFG["paper"], self.dir)
        self.assertIn("TOK", again.positions)
        self.assertAlmostEqual(again.balance, CFG["paper"]["starting_balance_usd"] - CFG["paper"]["position_size_usd"])


if __name__ == "__main__":
    unittest.main()
