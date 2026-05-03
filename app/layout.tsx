import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "GoldEdge — Algorithmic Gold (XAU/USD) Strategy | Profit Factor 3.56",
  description:
    "A backtested, rule-based long-only Gold strategy. 66.67% win rate, 3.56 profit factor, 0.01% max drawdown. One-time license. No subscription.",
  openGraph: {
    title: "GoldEdge — Algorithmic Gold Strategy",
    description:
      "Backtested rule-based Gold strategy. PF 3.56 · Win 66.67% · Max DD 0.01%.",
    type: "website",
  },
};

export const viewport: Viewport = {
  themeColor: "#0A0B0C",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="bg-ink-900 text-[#E8E6E1]">{children}</body>
    </html>
  );
}
