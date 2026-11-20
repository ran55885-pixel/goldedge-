# Aurum Nexus - architecture

```
                        ┌──────────────────────── hub process ────────────────────────┐
 MT5 terminal A ── worker A (Mt5Adapter) ─┐   PriceFeed (MT5 analysis terminal → OANDA → fallback)
 MT5 terminal B ── worker B (Mt5Adapter) ─┼─▶ Engine.cycle():
   (one portable terminal + one process   │     ACCOUNTS.sync → SCANNER → LEVELS(+DXY) → live/paper gating
    per account; queue-based WorkerProxy) │     → TRADE MANAGER (open trades) → EXECUTION pending
 OANDA v20 (REST + stream) ───────────────┤     → SIGNAL (M1/M5/M15) → COORDINATOR → GUARDIAN veto → RISK sizing
 Swissquote + Yahoo (PREVIEW) ────────────┘     → EXECUTION (per account, re-based levels) → shadows → ANALYST/STRATEGIST
                                             SQLite: signals, decisions, trades, fills, equity, events, profiles, users
                                             FastAPI + WebSocket ─▶ dashboard (browser / desktop window / phone)
```

* **Signal once, execute many**: candidates come from the analysis feed; each account re-bases entry/SL/TP by its own
  broker mid offset and sizes with its own equity, `trade_tick_value`/`trade_tick_size` and volume rules.
* **Paper** = `PaperBroker`, simulated fills on the account's own terminal quotes (or the feed). Real MT5 figures stay
  the main numbers; paper is labelled separately.
* **Backtester** drives the very same `Engine` with a `BacktestAdapter` over a `HistoryStore` (no look-ahead: an M1 bar
  is visible only after it closed; higher TFs are resampled; the forming bar is built from completed M1 bars).

## Score (Signal bot)
Weights (sum 100, `signal.weights`): zone_quality 15 · confluence 15 · confirmation 15 · bias 15 · volume_flow 20 ·
proximity 5 · stop_quality 10 · conditions 5. Each component is 0..1 and documented in `aurum_nexus/bots/signal.py`.
Bounded modifiers (`signal.modifier_caps`): levels_ready +8/-8 (STALLED), dxy ±10, regime ±5, sentiment_macro ±5,
seasonality ±5, analyst ±15, playbook weight; total modifiers capped at ±25. Final score clamped 0..100; min 70 (floor).

## Safety floors (enforced in code)
`config.enforce_floors`: min_rr ≥ 1.0, min_score ≥ 70, tp1_rr ≥ min_rr, refuse_live_on_fallback always true.
`strategist.clamp_profile`: bounded parameter ranges, forbidden keys (min_rr, min_score, guardian, risk…) rejected.
`config.effective_live`: every live gate; unknown account trade mode is treated as REAL.

## Tests
`pytest` (≈145 tests): indicators, DXY formula, risk sizing & RR floor incl. spread and post-fill, Guardian rules, levels
state machine incl. STALLED/BROKEN, ranking, Trade Manager & time targets, execution (filling modes, retries,
slippage, deferral), accounts reconnect, analyst/strategist bounds, intel degradation, adapters (strict fake of the
official MetaTrader5 package, OANDA v20 samples, real saved public-feed samples), backtester/optimizer/CLI, API & auth,
and build guards (no moving averages, no hard-coded price levels, no secrets).
