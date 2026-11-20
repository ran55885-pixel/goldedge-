# AURUM NEXUS

Multi-agent, multi-account MetaTrader 5 day-trading system for gold (XAUUSD) and forex, with a neon HUD
dashboard, a Windows desktop app and secure LAN/phone access. **Full user guide (Hebrew): [README_he.md](README_he.md).**

- 9 cooperating bots: SCANNER, LEVELS, SIGNAL, RISK (+ Trade Manager), EXECUTION, GUARDIAN, ACCOUNTS, ANALYST, STRATEGIST
- Paper by default; live needs `system.mode=live` + `allow_live` (+ `allow_real` for REAL accounts) + typing `I UNDERSTAND`
- Hard floors: min RR 1:1, min score 70, Guardian veto, refusal to trade live on fallback data
- MA-free by design (a test fails the build if any moving average appears)
- No profit promises. No fake numbers: missing data is shown as STALE / DISCONNECTED / NO DATA / n/a

```bash
pip install -r requirements.txt
python -m aurum_nexus check --network
python -m aurum_nexus --accounts config/accounts.demo.yaml run      # demo (paper, public preview data)
python -m aurum_nexus backtest --csv-dir data/history --symbols XAUUSD,EURUSD
python -m pytest -q
```

Docs: [architecture](docs/ARCHITECTURE.md) · [backtest CSV format](docs/BACKTEST_CSV.md) ·
[remote access](docs/REMOTE_ACCESS.md) · [TradingView webhook](docs/TRADINGVIEW_WEBHOOK.md) ·
[sample backtest on real data](docs/reports/)
