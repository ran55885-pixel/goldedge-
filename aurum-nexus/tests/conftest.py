import os
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("AURUM_TESTING", "1")

T0 = 1_700_006_400  # 2023-11-15 00:00 UTC (a Wednesday)


def make_bars(rows, start=T0, step=60, vol=100.0):
    """rows: list of (o,h,l,c) or (o,h,l,c,v)."""
    out = []
    for i, r in enumerate(rows):
        o, h, low, c = r[:4]
        v = r[4] if len(r) > 4 else vol
        out.append({"time": start + i * step, "open": o, "high": h, "low": low, "close": c, "tick_volume": v,
                    "real_volume": 0.0, "spread": 0.0})
    return pd.DataFrame(out)


def path_bars(prices, start=T0, step=60, wick=0.2, vol=100.0):
    """Bars whose close follows `prices`; open = previous close."""
    rows = []
    prev = prices[0]
    for p in prices:
        o, c = prev, p
        rows.append((o, max(o, c) + wick, min(o, c) - wick, c, vol))
        prev = p
    return make_bars(rows, start, step)


@pytest.fixture
def cfg():
    from aurum_nexus.config import config_from_dict
    return config_from_dict({}, [{"name": "PAPER-1", "kind": "paper_only", "starting_balance": 10000}])
