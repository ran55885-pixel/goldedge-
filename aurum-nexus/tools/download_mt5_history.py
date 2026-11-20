"""Windows tool: download M1 history from an MT5 terminal into backtest CSVs.

Usage (from the project folder, venv active):
    python tools/download_mt5_history.py --account icm-demo --symbols XAUUSD,EURUSD --start 2024-01-01
Equivalent to:  mt5sys download-history --source mt5 --account icm-demo ...
Uses the official MetaTrader5 copy_rates_range in 30-day chunks; server time -> UTC via the account's
server_utc_offset_hours. Output: data/history/<SYMBOL>.csv (format: docs/BACKTEST_CSV.md).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum_nexus.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["download-history", "--source", "mt5", *sys.argv[1:]]))
