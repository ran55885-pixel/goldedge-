"""Generate SIMULATED M1 CSVs (clearly labelled) for testing the backtester without market data.

    python tools/make_synthetic_csv.py --out data/synthetic --days 10 --symbols XAUUSD,EURUSD,USDJPY
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum_nexus.backtest.csvio import write_m1_csv  # noqa: E402
from aurum_nexus.backtest.synthetic import generate_m1  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/synthetic")
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--symbols", default="XAUUSD,EURUSD,GBPUSD,USDJPY,USDCAD,USDSEK,USDCHF")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--start", type=int, default=None, help="epoch start (default: days ago)")
    a = ap.parse_args()
    start = a.start or int(time.time()) - (a.days + 3) * 86400
    for i, s in enumerate(a.symbols.split(",")):
        df = generate_m1(s, start, a.days * 1440, seed=a.seed + i)
        p = Path(a.out) / f"{s}.csv"
        write_m1_csv(df, p, "SIMULATED DATA - synthetic random-walk generator, NOT market data")
        print(p, len(df))


if __name__ == "__main__":
    main()
