---
name: memecoin-research
description: Due diligence on a memecoin (Solana or Base) from a contract address, ticker or DexScreener link. Checks rug risk, holders, liquidity, momentum, news and community calls, weights every source by reliability and gives a verdict. Use when the user asks "check this coin", "is X a rug", "what's the news on X", "who is calling X", "should I enter", pastes a contract address, or asks what is moving in memecoins.
---

# Memecoin research

Goal: a fast, honest verdict on ONE coin, built only from sources you actually read in this session.
Never present a community claim as fact. Never promise a pump. Reply in the user's language (Hebrew by default).

## 1. Identify the exact coin
- Tickers are not unique (there are hundreds of "$PEPE"). Work from the **contract address**.
- If you only have a ticker: `curl -s "https://api.dexscreener.com/latest/dex/search?q=TICKER"`, list the top 3 by liquidity
  and ask which one, or take the most liquid and say so.
- Official links (X, Telegram, website) come from the DexScreener `info` block of that address. A link found
  anywhere else is unverified.

## 2. Hard data (truth tier: on-chain / exchange)
Run from `memescan/`:
- Market: `curl -s https://api.dexscreener.com/tokens/v1/<chain>/<address>`: price, FDV, liquidity, age,
  5m/1h/6h/24h change, buys vs sells.
- Rug checks:
  - Solana: `curl -s https://api.rugcheck.xyz/v1/tokens/<mint>/report/summary`. Any `danger` risk = red flag.
    RugCheck does NOT see a dev who simply holds a big bag: for pump.fun coins also check the dev's launch buy
    (launch mode logs it) and top holders.
  - Base: `curl -s "https://api.gopluslabs.io/api/v1/token_security/8453?contract_addresses=<addr>"`.
    honeypot, cannot_sell_all, is_mintable, hidden_owner, owner_change_balance, tax > 10% = do not touch.
- Scanner view: `python -m memescan scan -v` shows how the scoring sees it, and why a coin was filtered out.

## 3. News and calls, weighted by reliability
| Tier | Sources | How to treat it |
|---|---|---|
| exchange (1.0) | Binance/Coinbase/Upbit/Bithumb market lists and announcements | Fact. A real listing is the strongest catalyst there is. |
| news (0.85) | CoinDesk, The Block, Decrypt, Blockworks, DL News, Cointelegraph | Reliable on facts; slow on memecoins. |
| aggregator (0.6) | CoinGecko trending, CryptoPanic | Attention signal, not a fact. |
| community (0.3) | Reddit, 4chan /biz/, public Telegram, X | Early but often paid or coordinated. Only an input, never proof. |

- If the intel engine has been running: `python -m memescan intel --sources` gives each source's MEASURED edge
  (how often its first call was followed by +50% in 1h or x2 in 24h) and its rug rate. Quote these numbers
  instead of guessing who is "reliable".
- Ask the intel DB who called the coin:
  `sqlite3 memescan/data/intel.db "select source, datetime(ts,'unixepoch'), price from mentions where token='<addr>' order by ts"`
- Several *independent* sources in a short window matters more than one loud one. Accounts that all post the
  same contract within seconds are one group (coordinated), not many.
- Web search, if available: look for the contract address itself, not the ticker.

## 4. Red flags (any one = say it loudly)
- Dev bought more than 8% at launch, or sold early
- Top 10 holders own more than 30% (excluding the pool and burn addresses)
- Liquidity under $15k, or liquidity falling while the price rises
- Sells outnumber buys in the last 5m after a big run
- Only community calls, and every caller posted within the same minute
- Socials created in the last 24h, or no socials at all

## 5. Verdict format
1. **One line:** AVOID / WATCH / CANDIDATE, with the main reason.
2. Numbers: FDV, liquidity, age, 1h/24h change, buys:sells.
3. Safety: pass or fail, and which checks.
4. Who is talking about it: each source with its tier and measured edge, if known.
5. If CANDIDATE: the plan in memescan terms (stop −30 to −35%, sell half at x2, trailing stop on the rest) and
   a position size the user can afford to lose entirely.

Never place a real order from this skill. Real execution is a separate step that the user must approve explicitly.
