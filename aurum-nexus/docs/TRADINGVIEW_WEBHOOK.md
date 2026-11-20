# TradingView webhook alerts (optional extra input)

TradingView has no official data API and Aurum Nexus never scrapes it. You can, however, send **alerts** to the hub.
An alert is an *extra input*: it only adds a small bounded note (±1.5 inside the ±5 sentiment/macro cap) to
candidates on the same symbol for 15 minutes. **An alert never opens a trade by itself**; every Guardian/risk rule applies.

1. In `.env`: `TRADINGVIEW_WEBHOOK_SECRET=<long random string>` and restart.
2. The hub must be reachable by TradingView over HTTPS (Cloudflare Tunnel, see REMOTE_ACCESS.md).
3. Alert -> Notifications -> Webhook URL: `https://<your-host>/api/webhook/tradingview`
4. Message (JSON):
```json
{"secret": "<same secret>", "symbol": "{{ticker}}", "direction": "buy", "message": "{{strategy.order.comment}}"}
```
`direction`: buy|sell|long|short. `symbol` may include a prefix (`OANDA:XAUUSD`). Wrong secret -> HTTP 403;
the endpoint is rate-limited (30/min per IP). Every alert is logged in the activity log.
