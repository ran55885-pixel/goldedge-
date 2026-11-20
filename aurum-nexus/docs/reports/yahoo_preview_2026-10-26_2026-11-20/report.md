# Aurum Nexus backtest report

> Historical simulation. Past results do not predict future results. No profit promise.

Period: 2026-10-26 11:53 → 2026-11-20 11:52 UTC · symbols XAUUSD, EURUSD, GBPUSD, USDJPY · step 5m

Balance 10,000.00 → 8,948.56

**Overall:** 95 trades · WR 26.3% · exp -0.340R · PF 0.39 · maxDD 32.33R · total -32.33R

RR floor check: min planned RR = 1.0 → OK

In-sample (until 2026-11-12 23:52): 65 trades · WR 27.7% · exp -0.344R · PF 0.44 · maxDD 22.35R · total -22.35R  
Out-of-sample: 30 trades · WR 23.3% · exp -0.333R · PF 0.26 · maxDD 10.24R · total -9.98R

## Trades per day per symbol

- XAUUSD: 3.02
- USDJPY: 2.3

## By Setup

| key | result |
|---|---|
| bos_retest | 18 trades · WR 22.2% · exp -0.739R · PF 0.30 · maxDD 15.53R · total -13.29R |
| fvg_ob | 13 trades · WR 15.4% · exp -0.230R · PF 0.48 · maxDD 2.99R · total -2.99R |
| orb | 1 trades · WR 0.0% · exp -1.021R · PF 0.00 · maxDD 1.02R · total -1.02R |
| second_leg | 4 trades · WR 25.0% · exp -0.402R · PF 0.24 · maxDD 2.01R · total -1.61R |
| sweep_reversal | 34 trades · WR 29.4% · exp -0.231R · PF 0.48 · maxDD 7.86R · total -7.86R |
| zone_reaction | 25 trades · WR 32.0% · exp -0.222R · PF 0.45 · maxDD 5.93R · total -5.55R |

## By Session

| key | result |
|---|---|
| asia | 22 trades · WR 13.6% · exp -0.702R · PF 0.14 · maxDD 15.78R · total -15.44R |
| london | 49 trades · WR 30.6% · exp -0.224R · PF 0.51 · maxDD 12.26R · total -11.00R |
| newyork | 4 trades · WR 25.0% · exp -0.215R · PF 0.37 · maxDD 1.26R · total -0.86R |
| overlap | 20 trades · WR 30.0% · exp -0.252R · PF 0.56 · maxDD 5.42R · total -5.04R |

## By Symbol

| key | result |
|---|---|
| USDJPY | 41 trades · WR 12.2% · exp -0.639R · PF 0.13 · maxDD 26.2R · total -26.20R |
| XAUUSD | 54 trades · WR 37.0% · exp -0.113R · PF 0.74 · maxDD 6.67R · total -6.13R |

## By Timeframe

| key | result |
|---|---|
| M1 | 34 trades · WR 29.4% · exp -0.529R · PF 0.28 · maxDD 18.7R · total -17.98R |
| M15 | 13 trades · WR 23.1% · exp -0.338R · PF 0.40 · maxDD 6.35R · total -4.39R |
| M5 | 48 trades · WR 25.0% · exp -0.207R · PF 0.52 · maxDD 9.95R · total -9.95R |

## By Exit type

| key | result |
|---|---|
| breakeven | 4 trades · WR 100.0% · exp +0.570R · PF inf · maxDD 0.0R · total +2.28R |
| early | 13 trades · WR 76.9% · exp +0.720R · PF 10.35 · maxDD 0.61R · total +9.36R |
| postfill | 18 trades · WR 0.0% · exp -0.121R · PF 0.00 · maxDD 2.18R · total -2.18R |
| sl | 39 trades · WR 0.0% · exp -1.173R · PF 0.00 · maxDD 45.73R · total -45.73R |
| time | 18 trades · WR 44.4% · exp -0.036R · PF 0.85 · maxDD 1.88R · total -0.65R |
| tp1 | 1 trades · WR 100.0% · exp +2.162R · PF inf · maxDD 0.0R · total +2.16R |
| tp2 | 2 trades · WR 100.0% · exp +1.215R · PF inf · maxDD 0.0R · total +2.43R |

## Walk-forward windows (out-of-sample segments)

| from | to | result |
|---|---|---|
| 2026-10-26 11:53 | 2026-10-31 11:53 | 29 trades · WR 24.1% · exp -0.193R · PF 0.57 · maxDD 6.56R · total -5.59R |
| 2026-10-31 11:53 | 2026-11-05 11:53 | 15 trades · WR 26.7% · exp -0.406R · PF 0.30 · maxDD 6.5R · total -6.10R |
| 2026-11-05 11:53 | 2026-11-10 11:53 | 14 trades · WR 28.6% · exp -0.335R · PF 0.47 · maxDD 4.99R · total -4.70R |
| 2026-11-10 11:53 | 2026-11-15 11:53 | 12 trades · WR 41.7% · exp -0.587R · PF 0.36 · maxDD 10.8R · total -7.05R |
| 2026-11-15 11:53 | 2026-11-20 11:52 | 25 trades · WR 20.0% · exp -0.356R · PF 0.22 · maxDD 8.91R · total -8.91R |

## Time targets: with vs without + N/T1/T2 grid

| variant | result |
|---|---|
| time_targets ON (baseline) | 95 trades · WR 26.3% · exp -0.340R · PF 0.39 · maxDD 32.33R · total -32.33R |
| time_targets OFF | 106 trades · WR 27.4% · exp -0.260R · PF 0.52 · maxDD 29.47R · total -27.56R |
| N=2 T1x1.5 T2x1.5 | 94 trades · WR 26.6% · exp -0.387R · PF 0.35 · maxDD 36.38R · total -36.38R |
| N=2 T1x2.5 T2x3.0 | 94 trades · WR 26.6% · exp -0.387R · PF 0.35 · maxDD 36.38R · total -36.38R |
| N=4 T1x1.5 T2x1.5 | 100 trades · WR 27.0% · exp -0.298R · PF 0.43 · maxDD 30.68R · total -29.76R |
| N=4 T1x2.5 T2x3.0 | 100 trades · WR 27.0% · exp -0.298R · PF 0.43 · maxDD 30.68R · total -29.76R |

## Trade Manager rules fired

- ltf_choch: 2
- time_t1: 14
- time_t2: 18
- opposing_rejection: 2
- giveback: 10
- vwap_band_rejection: 2
- dxy_shock: 1
- momentum_fade: 1

## Assumptions

- Identical live pipeline (Engine) on historical M1; cycle 6 s after each M1 close.
- Intra-bar path O -> adverse extreme -> favourable extreme -> C (SL assumed before TP in the same bar).
- Gaps through the stop fill at the bar open; TP/limit exits fill at their price.
- Spread from CSV (points) else default; commission per lot round-turn; stop/market slippage in points.
- R multiples are price-based (spread & slippage included); commission is in the money P/L (pnl_net).
- No historical news/COT/macro/sentiment/DOM in backtests (filters inactive unless manual blackouts).
