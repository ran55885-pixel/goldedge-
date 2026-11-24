---
name: memescan-operator
description: Run, monitor, tune and troubleshoot the MemeScan system in memescan/ (scanner, launch radar, intel engine, paper trading). Use when the user asks to start or stop the scanner, "how is the bot doing", "show results", "why didn't it buy X", "make it more or less aggressive", "add a news source or Telegram channel", "which sources are reliable", or to change entry and exit rules.
---

# MemeScan operator

The code is in `memescan/`, settings in `memescan/config.yaml`, data in `memescan/data/` (or `$MEMESCAN_DATA`).

## Commands
| What | Command |
|---|---|
| One scan of established pools | `python -m memescan scan [-v]` |
| Scanner loop + paper trading | `python -m memescan run` |
| Live pump.fun launch radar + paper trading | `python -m memescan launch [--minutes N]` |
| News, listings and community intel | `python -m memescan intel [--once]` |
| Source reliability table | `python -m memescan intel --sources` |
| Results | `python -m memescan stats` / `stats --launch` |
| Tests | `python -m unittest discover -s tests` |

Run `intel` next to `launch`: launch mode reads `data/intel.db` and adds points when independent sources call the
same contract.

## Reading results honestly
- Fewer than 50 closed paper trades means no conclusion yet. Say so.
- Report win rate AND average win vs average loss. Memecoin strategies win less than half the time and live on
  the few big winners.
- Paper fills are optimistic: real slippage, priority fees and failed transactions in the first minutes are worse.
  A paper edge under ~20% is probably zero in real life.
- Look at `data/launch/paper_trades.csv` for the WORST trades and why they happened, not only the PnL total.

## Tuning: one change at a time, then re-measure
- Too many −80%+ losers right after entry: raise `launch.min_age_seconds` or `confirm_polls`, and lower
  `max_dev_buy_pct`.
- Missing the big runners: lower `launch.min_mcap_multiple` or `entry_min_score`, but only if losers stay controlled.
- Exits too early on winners: raise `trailing_stop_pct`. Giving back too much: lower it.
- Record every change and the reason in the commit message, so results can be compared before and after.

## Sources
- Add a public Telegram channel: append its username (the part after t.me/) to
  `intel.sources.telegram_public.channels`.
- Add a news site: add `Name: <rss url>` under `intel.sources.news_rss.feeds`.
- Private groups: only through the user's own Telegram account (Telethon with their api_id/api_hash), and only
  for groups they are already a member of. Never scrape or join groups on their behalf without that.
- Down sources are printed as `[sources down]`. A 403 from the agent proxy means the host isn't allowed in this
  cloud environment. Read the environment documentation and propose the exact hosts; on the user's own
  machine there is no such limit.

## Real money
Paper mode only, until the user explicitly asks for live trading after reviewing at least 50 paper trades.
Live trading needs a separate small wallet, a hard daily loss limit and a per-order confirmation.
Never ask for a private key in chat: it goes in an environment variable on the user's machine.
