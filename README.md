# GoldEdge — Landing Page

High-conversion landing page for the **GoldEdge** algorithmic XAU/USD strategy. Built with Next.js 14 (App Router), TypeScript, and Tailwind CSS. Designed to deploy to Vercel as-is.

## One-click Vercel deploy

1. Push this repository to GitHub (already on branch `claude/landing-page-converter-HdKOz`).
2. Go to [vercel.com/new](https://vercel.com/new) and import the repo.
3. Framework preset: **Next.js** (auto-detected). No env vars required.
4. Click **Deploy**. Done — production URL in ~60 seconds.

## Local development

```bash
npm install
npm run dev
# open http://localhost:3000
```

## Where to edit copy / numbers

All marketing copy and the strategy stats live in `app/page.tsx`:

- `STATS` — hero metric cards (PF, win rate, drawdown, P&L)
- `FEATURES` — strategy bullets
- `FAQ` — buyer objections
- Pricing block — currently `$197` with a struck-through `$397` anchor

## Suggested target audience

- **Forex / Gold day-traders** already running TradingView (the indicator deploys via Pine Script)
- **Prop-firm traders** who need rule-based, low-drawdown systems to clear evaluations
- **Systematic / quant-curious retail traders** who read profit factor and drawdown before they read a sales page
- **Buyers, not browsers**: people who have paid $97–$497 for indicators or EAs before and were burned by curve-fit vendors

### Where to advertise

- TradingView script comments + paid Lightweight ads
- Reddit: r/Forex, r/Daytrading, r/algotrading (organic posts with chart screenshots)
- YouTube pre-roll on Gold/XAU/USD trading channels
- X/Twitter FinTwit (cashtag $XAUUSD)
- Niche Discord servers for prop-firm challenge traders (FTMO, MyForexFunds successors, The5ers, etc.)

### Pricing rationale

A profit factor of 3.56 with sub-0.1% drawdown is the kind of metric serious traders price seriously. **$197 one-time** sits in the &ldquo;take-it-now&rdquo; impulse band — high enough to look credible vs. $19 junk indicators, low enough to skip a pricing committee meeting. The struck-through $397 anchor is your headroom to test &ldquo;launch ends Friday&rdquo; scarcity later.
