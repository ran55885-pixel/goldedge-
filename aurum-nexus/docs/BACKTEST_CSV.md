# Backtest CSV format (M1)

One file per symbol, named `<SYMBOL>.csv` (e.g. `XAUUSD.csv`; broker suffixes are stripped: `XAUUSD.m.csv` works).
Optional `DXY.csv` (or `--dxy-csv path`) is used as the broker DXY symbol; otherwise the synthetic ICE index is
built from EURUSD, USDJPY (+ GBPUSD, USDCAD, USDSEK, USDCHF if present).

| column | required | meaning |
|---|---|---|
| `time` | yes | bar OPEN time in **UTC**: `YYYY-MM-DD HH:MM:SS`, ISO-8601, or epoch seconds/milliseconds |
| `open`,`high`,`low`,`close` | yes | prices (broker prices for best fidelity) |
| `tick_volume` | no | MT5 tick volume (0 if unknown -> volume tools disabled) |
| `real_volume` | no | exchange volume when available (used instead of tick volume when non-zero) |
| `spread` | no | spread in **points** at the bar (else `backtest.default_spread_points`) |

Lines starting with `#` are comments (downloaders write the data source there). Rows with high/low inconsistent with
open/close are rejected. Example:

```
# source: MT5 ICMarketsSC-Demo XAUUSD (broker prices, UTC, spread in points)
time,open,high,low,close,tick_volume,real_volume,spread
2024-03-04 08:00:00,2083.15,2084.02,2082.90,2083.77,412,0,12
```

Create files with `mt5sys download-history --source mt5|oanda|yahoo` or `tools/download_mt5_history.py`.
`tools/make_synthetic_csv.py` writes clearly labelled SIMULATED files for testing only.
