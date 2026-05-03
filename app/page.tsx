import Link from "next/link";

const STATS = [
  { label: "Profit Factor", value: "3.564", sub: "Gross profit ÷ gross loss" },
  { label: "Win Rate", value: "66.67%", sub: "24 of 36 trades" },
  { label: "Max Equity Drawdown", value: "0.01%", sub: "$76.69 worst dip" },
  { label: "Net P&L (Backtest)", value: "+$1,015.25", sub: "Jan 12 – Apr 24, 2026" },
];

const FEATURES = [
  {
    title: "Long-only on Gold (XAU/USD)",
    body: "No shorts. No counter-trend trades. The system waits for confirmed bullish structure above the 200 MA — then triggers entries with MACD-style momentum (13/26/8).",
  },
  {
    title: "Hard-coded risk per trade",
    body: "Stop-loss 330 pips, take-profit 550 pips. A fixed ~1:1.67 R:R baked in. No discretion. No moving stops. No revenge trading.",
  },
  {
    title: "Session filter (08:00 – 22:00)",
    body: "Trades only inside London + NY liquidity windows. Avoids the spread-widening, news-gap, low-volume dead zones that destroy most retail systems.",
  },
  {
    title: "Plug-and-play indicator",
    body: "Drop it on your TradingView chart. Alerts fire on every signal. Connect to your broker via webhook or trade manually — both supported.",
  },
];

const FAQ = [
  {
    q: "Is this a signals service or an indicator?",
    a: "Both. You get the full strategy script (indicator + alerts + backtester) — installed once on your TradingView account. No monthly Telegram channel. No human in the loop.",
  },
  {
    q: "What instrument and timeframe does it run on?",
    a: "Designed and tuned for XAU/USD (Gold) on the H1 timeframe. The logic generalizes to other major instruments, but published metrics reflect Gold H1.",
  },
  {
    q: "What does the published track record cover?",
    a: "Backtest from Jan 12, 2026 to Apr 24, 2026 — 36 closed trades, 66.67% win rate, 3.564 profit factor, $76.69 max equity drawdown. Forward results will vary.",
  },
  {
    q: "Do I need a special broker?",
    a: "No. Anything with reasonable Gold spreads (ICMarkets, Pepperstone, OANDA, Tickmill, etc.). For automation, any broker that accepts TradingView webhooks works.",
  },
  {
    q: "Is there a refund policy?",
    a: "14-day refund. If the script doesn't install, doesn't fire alerts, or doesn't match the documented logic — full refund, no questions.",
  },
  {
    q: "Why one-time pricing?",
    a: "We don't want recurring revenue from people who aren't trading. Pay once. Own the script. Update path included for 12 months.",
  },
];

function CheckIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden>
      <path
        d="M5 12.5l4.5 4.5L19 7.5"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default function Page() {
  return (
    <main className="relative min-h-screen overflow-x-hidden">
      {/* NAV */}
      <header className="sticky top-0 z-40 backdrop-blur bg-ink-900/70 border-b border-white/5">
        <div className="mx-auto max-w-6xl px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-md border border-gold-300/60 grid place-items-center">
              <span className="font-display text-gold-200 text-sm">GE</span>
            </div>
            <span className="font-display tracking-[0.18em] text-sm text-gold-100">
              GOLDEDGE
            </span>
          </div>
          <nav className="hidden md:flex items-center gap-7 text-sm text-white/70">
            <a href="#metrics" className="hover:text-white">Metrics</a>
            <a href="#strategy" className="hover:text-white">Strategy</a>
            <a href="#pricing" className="hover:text-white">Pricing</a>
            <a href="#faq" className="hover:text-white">FAQ</a>
          </nav>
          <a
            href="#pricing"
            className="text-sm px-4 py-2 rounded-md bg-gold-300 text-ink-900 font-semibold hover:bg-gold-200 transition"
          >
            Get access
          </a>
        </div>
      </header>

      {/* HERO */}
      <section className="grid-bg relative">
        <div className="mx-auto max-w-6xl px-6 pt-20 pb-24 md:pt-28 md:pb-32">
          <div className="inline-flex items-center gap-2 rounded-full border border-gold-300/30 bg-gold-300/5 px-3 py-1 text-xs uppercase tracking-widest text-gold-200 fade-up">
            <span className="w-1.5 h-1.5 rounded-full bg-gold-300 animate-pulse" />
            Live backtest · Jan – Apr 2026 · XAU/USD
          </div>

          <h1 className="mt-6 font-display text-4xl md:text-6xl leading-[1.05] max-w-3xl fade-up">
            A Gold strategy that <span className="gold-text">prints 3.56× more</span> than it loses —
            with a 0.01% max drawdown.
          </h1>

          <p className="mt-6 max-w-2xl text-white/70 text-lg fade-up">
            GoldEdge is a long-only, rule-based XAU/USD system. No discretion, no curve-fit
            optimization, no monthly fee. Install it on TradingView in five minutes — own the script
            for life.
          </p>

          <div className="mt-8 flex flex-wrap items-center gap-4 fade-up">
            <a
              href="#pricing"
              className="px-6 py-3.5 rounded-md bg-gold-300 text-ink-900 font-semibold hover:bg-gold-200 transition shadow-gold"
            >
              Get GoldEdge — $197 one-time
            </a>
            <a
              href="#metrics"
              className="px-6 py-3.5 rounded-md border border-white/15 text-white/85 hover:bg-white/5 transition"
            >
              See the numbers ↓
            </a>
          </div>

          <div className="mt-10 flex flex-wrap items-center gap-x-6 gap-y-3 text-sm text-white/55 fade-up">
            <span className="flex items-center gap-2">
              <CheckIcon className="w-4 h-4 text-gold-300" /> 14-day refund
            </span>
            <span className="flex items-center gap-2">
              <CheckIcon className="w-4 h-4 text-gold-300" /> Lifetime license
            </span>
            <span className="flex items-center gap-2">
              <CheckIcon className="w-4 h-4 text-gold-300" /> Source-protected Pine Script
            </span>
            <span className="flex items-center gap-2">
              <CheckIcon className="w-4 h-4 text-gold-300" /> TradingView alerts + webhooks
            </span>
          </div>
        </div>
      </section>

      {/* METRICS */}
      <section id="metrics" className="mx-auto max-w-6xl px-6 pb-20">
        <div className="rounded-2xl border border-white/10 bg-gradient-to-b from-ink-700 to-ink-800 p-8 md:p-10">
          <div className="flex items-center justify-between flex-wrap gap-4">
            <div>
              <div className="text-xs uppercase tracking-widest text-white/40">
                Strategy Report
              </div>
              <h2 className="font-display text-2xl md:text-3xl mt-1">
                GoldEdge — verified backtest
              </h2>
            </div>
            <div className="text-sm text-white/55 flex items-center gap-2">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                <rect x="3" y="5" width="18" height="16" rx="2" stroke="currentColor" strokeWidth="1.6" />
                <path d="M3 9h18M8 3v4M16 3v4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              </svg>
              Jan 12, 2026 – Apr 24, 2026
            </div>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-8">
            {STATS.map((s) => (
              <div
                key={s.label}
                className="rounded-xl border border-white/10 bg-ink-900/60 p-5"
              >
                <div className="text-xs uppercase tracking-widest text-white/45">
                  {s.label}
                </div>
                <div className="mt-2 font-display text-2xl md:text-3xl gold-text">
                  {s.value}
                </div>
                <div className="text-xs text-white/45 mt-1">{s.sub}</div>
              </div>
            ))}
          </div>

          {/* Equity chart mock */}
          <div className="mt-8 rounded-xl border border-white/10 bg-ink-900/60 p-6">
            <div className="flex items-center justify-between mb-4">
              <div className="text-sm text-white/65">Equity curve</div>
              <div className="text-sm gold-text font-semibold">+$1,015.25</div>
            </div>
            <svg viewBox="0 0 600 180" className="w-full h-44">
              <defs>
                <linearGradient id="eq" x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0%" stopColor="#21C383" stopOpacity="0.35" />
                  <stop offset="100%" stopColor="#21C383" stopOpacity="0" />
                </linearGradient>
              </defs>
              {[40, 80, 120].map((y) => (
                <line
                  key={y}
                  x1="0"
                  x2="600"
                  y1={y}
                  y2={y}
                  stroke="rgba(255,255,255,0.05)"
                />
              ))}
              <path
                d="M0,160 L40,150 L80,140 L120,135 L160,120 L200,105 L240,95 L280,80 L320,90 L360,70 L400,55 L440,60 L480,40 L520,30 L560,25 L600,20"
                fill="none"
                stroke="#21C383"
                strokeWidth="2.4"
                className="equity-line"
              />
              <path
                d="M0,160 L40,150 L80,140 L120,135 L160,120 L200,105 L240,95 L280,80 L320,90 L360,70 L400,55 L440,60 L480,40 L520,30 L560,25 L600,20 L600,180 L0,180 Z"
                fill="url(#eq)"
              />
            </svg>
          </div>

          <p className="text-xs text-white/40 mt-4 leading-relaxed">
            Backtest results based on historical XAU/USD H1 data (Jan 12 – Apr 24, 2026), 36 closed
            trades. Past performance is not a guarantee of future results. Trading leveraged
            instruments carries substantial risk of loss.
          </p>
        </div>
      </section>

      {/* STRATEGY DETAIL */}
      <section id="strategy" className="mx-auto max-w-6xl px-6 pb-24">
        <div className="grid md:grid-cols-2 gap-10 items-start">
          <div>
            <div className="text-xs uppercase tracking-widest text-gold-300">
              Under the hood
            </div>
            <h2 className="font-display text-3xl md:text-4xl mt-3 leading-tight">
              No black box. Every parameter is on the table.
            </h2>
            <p className="text-white/65 mt-4">
              We don&rsquo;t hide the logic behind a paywall. The strategy uses a fast/slow
              MACD-style oscillator filtered by a 200 MA trend regime, a session-time gate, and a
              fixed risk frame. That&rsquo;s it.
            </p>

            <div className="mt-6 space-y-4">
              {FEATURES.map((f) => (
                <div
                  key={f.title}
                  className="rounded-xl border border-white/10 bg-ink-700/40 p-5"
                >
                  <div className="flex items-start gap-3">
                    <CheckIcon className="w-5 h-5 mt-0.5 text-gold-300 flex-none" />
                    <div>
                      <div className="font-semibold text-white">{f.title}</div>
                      <div className="text-white/60 text-sm mt-1">{f.body}</div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Inputs panel mock */}
          <div className="rounded-2xl border border-white/10 bg-ink-700/60 p-6 md:sticky md:top-24">
            <div className="flex items-center justify-between mb-5">
              <div className="font-semibold text-white">GoldEdge strategy</div>
              <div className="text-xs text-white/45">Inputs</div>
            </div>

            <div className="space-y-5 text-sm">
              <Section title="General">
                <Row k="Source" v="Close" />
                <Row k="Bar Color" v="Enabled" />
                <Row k="Moving Averages" v="Enabled" />
                <Row k="LONG trades" v="Enabled" />
                <Row k="SHORT trades" v="Disabled" muted />
              </Section>

              <Section title="Long Parameters" dot="#21C383">
                <Row k="Fast / Slow / Signal" v="13 / 26 / 8" />
                <Row k="Very Slow MA" v="200" />
                <Row k="Hours Filter" v="08:00 – 22:00" />
                <Row k="Stop-Loss" v="330 pips" />
                <Row k="Take-Profit" v="550 pips" />
              </Section>

              <Section title="Short Parameters" dot="#E5484D" muted>
                <Row k="Fast / Slow / Signal" v="6 / 23 / 7" muted />
                <Row k="Very Slow MA" v="200" muted />
              </Section>
            </div>
          </div>
        </div>
      </section>

      {/* SOCIAL/PROOF STRIP */}
      <section className="border-y border-white/10 bg-ink-800/60">
        <div className="mx-auto max-w-6xl px-6 py-10 grid md:grid-cols-3 gap-8">
          <Quote
            stat="3.56×"
            label="profit-to-loss ratio"
            text="Most retail systems hover around 1.1 – 1.4 PF. Anything above 2.0 is rare. GoldEdge clears 3.5 — every losing trade is dwarfed by the winners."
          />
          <Quote
            stat="0.01%"
            label="max equity drawdown"
            text="Risk discipline is baked in. Even the worst losing streak in the test cost $76.69 against $1,015 of profit — a quiet equity curve, not a roller coaster."
          />
          <Quote
            stat="36"
            label="trades · 3.5 months"
            text="Roughly 10 trades a month. Selective. No overtrading, no FOMO, no &lsquo;just one more setup&rsquo;. The system either fires — or it sits on its hands."
          />
        </div>
      </section>

      {/* PRICING */}
      <section id="pricing" className="mx-auto max-w-6xl px-6 py-24">
        <div className="text-center max-w-2xl mx-auto">
          <div className="text-xs uppercase tracking-widest text-gold-300">
            One price. Lifetime access.
          </div>
          <h2 className="font-display text-4xl md:text-5xl mt-3">
            Pay once. <span className="gold-text">Trade it forever.</span>
          </h2>
          <p className="text-white/65 mt-4">
            No subscriptions, no &ldquo;pro tiers&rdquo;, no upsells. One license, one quiet
            checkout, the script lands in your inbox in under a minute.
          </p>
        </div>

        <div className="mt-12 grid place-items-center">
          <div className="relative w-full max-w-xl rounded-2xl border border-gold-300/40 bg-gradient-to-b from-ink-700 to-ink-800 p-8 md:p-10 shadow-gold">
            <div className="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-1 rounded-full bg-gold-300 text-ink-900 text-[11px] font-bold tracking-wider uppercase">
              Limited launch price
            </div>

            <div className="text-center">
              <div className="font-display text-lg text-white">GoldEdge — Full License</div>
              <div className="mt-4 flex items-baseline justify-center gap-3">
                <span className="text-white/40 line-through text-2xl">$397</span>
                <span className="font-display text-6xl gold-text">$197</span>
              </div>
              <div className="text-white/50 text-sm mt-1">
                One-time payment · USD · no recurring charges
              </div>
            </div>

            <ul className="mt-8 space-y-3 text-sm text-white/85">
              {[
                "Full TradingView strategy script (indicator + backtester)",
                "Pre-configured alert templates for broker webhooks",
                "Setup guide + suggested risk-per-trade sizing",
                "12 months of free script updates",
                "Private Discord channel for license holders",
                "14-day no-questions refund",
              ].map((item) => (
                <li key={item} className="flex items-start gap-3">
                  <CheckIcon className="w-5 h-5 text-gold-300 mt-0.5 flex-none" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>

            <a
              href="#checkout"
              className="mt-8 block text-center w-full px-6 py-4 rounded-md bg-gold-300 text-ink-900 font-bold hover:bg-gold-200 transition"
            >
              Get instant access — $197
            </a>
            <div className="text-center text-xs text-white/45 mt-4">
              Secure checkout · Stripe · Visa, Mastercard, Amex, Apple Pay
            </div>
          </div>
        </div>
      </section>

      {/* FAQ */}
      <section id="faq" className="mx-auto max-w-3xl px-6 pb-24">
        <h2 className="font-display text-3xl md:text-4xl text-center">
          Questions a serious trader asks
        </h2>
        <div className="mt-10 divide-y divide-white/10 border-y border-white/10">
          {FAQ.map((f, i) => (
            <details key={i} className="group py-5">
              <summary className="cursor-pointer list-none flex items-center justify-between gap-6">
                <span className="font-medium text-white/90">{f.q}</span>
                <span className="text-gold-300 text-xl transition group-open:rotate-45">+</span>
              </summary>
              <p className="mt-3 text-white/65 text-[15px] leading-relaxed">{f.a}</p>
            </details>
          ))}
        </div>
      </section>

      {/* FINAL CTA */}
      <section className="mx-auto max-w-5xl px-6 pb-28">
        <div className="rounded-2xl border border-gold-300/30 bg-gradient-to-br from-ink-700 to-ink-900 p-10 md:p-14 text-center">
          <h3 className="font-display text-3xl md:text-4xl">
            Stop renting signals. <span className="gold-text">Own the system.</span>
          </h3>
          <p className="text-white/65 mt-4 max-w-xl mx-auto">
            $197 today. No second invoice, ever. If GoldEdge isn&rsquo;t what we claim it is, you
            get every dollar back.
          </p>
          <a
            href="#pricing"
            className="inline-block mt-8 px-8 py-4 rounded-md bg-gold-300 text-ink-900 font-bold hover:bg-gold-200 transition shadow-gold"
          >
            Get GoldEdge — one-time $197
          </a>
        </div>
      </section>

      {/* FOOTER */}
      <footer className="border-t border-white/10">
        <div className="mx-auto max-w-6xl px-6 py-10 flex flex-col md:flex-row items-center justify-between gap-4 text-sm text-white/45">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded border border-gold-300/60 grid place-items-center">
              <span className="font-display text-gold-200 text-[10px]">GE</span>
            </div>
            <span>© {new Date().getFullYear()} GoldEdge. All rights reserved.</span>
          </div>
          <div className="text-xs text-white/40 max-w-xl text-center md:text-right">
            Risk warning: trading leveraged instruments such as Gold (XAU/USD) involves substantial
            risk and may result in the loss of your invested capital. Past performance is not
            indicative of future results.
          </div>
        </div>
      </footer>
    </main>
  );
}

function Section({
  title,
  children,
  dot = "#D4AF37",
  muted = false,
}: {
  title: string;
  children: React.ReactNode;
  dot?: string;
  muted?: boolean;
}) {
  return (
    <div className={muted ? "opacity-60" : ""}>
      <div className="flex items-center gap-2 mb-2">
        <span
          className="w-2 h-2 rounded-full"
          style={{ background: dot }}
          aria-hidden
        />
        <div className="text-[11px] uppercase tracking-widest text-white/45">
          {title}
        </div>
      </div>
      <div className="rounded-lg border border-white/10 bg-ink-900/40 divide-y divide-white/5">
        {children}
      </div>
    </div>
  );
}

function Row({
  k,
  v,
  muted = false,
}: {
  k: string;
  v: string;
  muted?: boolean;
}) {
  return (
    <div
      className={`flex items-center justify-between px-4 py-2.5 ${
        muted ? "text-white/45" : "text-white/85"
      }`}
    >
      <span className="text-sm text-white/55">{k}</span>
      <span className="text-sm font-mono">{v}</span>
    </div>
  );
}

function Quote({
  stat,
  label,
  text,
}: {
  stat: string;
  label: string;
  text: string;
}) {
  return (
    <div>
      <div className="font-display text-3xl gold-text">{stat}</div>
      <div className="text-xs uppercase tracking-widest text-white/40 mt-1">
        {label}
      </div>
      <p className="text-white/65 text-sm mt-3 leading-relaxed">{text}</p>
    </div>
  );
}
