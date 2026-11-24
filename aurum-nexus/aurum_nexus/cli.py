"""mt5sys command line: run | check | backtest | optimize | download-history"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from aurum_nexus import APP_NAME, __version__
from aurum_nexus.config import LIVE_CONFIRM_PHRASE, PROJECT_ROOT, load_config


def _ts(s: str | None) -> float | None:
    if not s:
        return None
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() if "T" in s or len(s) > 10 \
        else datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()


def _load(args):
    over = {}
    if getattr(args, "price_source", None):
        over.setdefault("system", {})["price_source"] = args.price_source
    if getattr(args, "port", None):
        over.setdefault("dashboard", {})["port"] = args.port
    if getattr(args, "host", None):
        over.setdefault("dashboard", {})["host"] = args.host
    return load_config(args.config_dir, accounts_file=getattr(args, "accounts", None), overrides=over or None)


# ------------------------------------------------------------------ run
def confirm_live(cfg, yes_live: bool, input_fn=input) -> bool:
    wants = cfg.mode == "live" and any(a.mode == "live" and a.allow_live for a in cfg.accounts)
    if not wants:
        return False
    if yes_live:
        return True
    print("\n!!! LIVE TRADING requested (system.mode=live and at least one account allow_live=true).")
    print("    Real orders may be sent. There is NO profit promise; you can lose money.")
    ans = input_fn(f'    Type "{LIVE_CONFIRM_PHRASE}" to enable live trading (anything else = paper): ')
    return ans.strip() == LIVE_CONFIRM_PHRASE


def cmd_run(args) -> int:
    cfg = _load(args)
    if args.simulate:
        print("*** SIMULATE mode: SIMULATED data only - nothing shown is market data ***")
    cfg.live_confirmed = confirm_live(cfg, args.yes_live) if not args.simulate else False
    from aurum_nexus.hub.server import serve
    return serve(cfg, simulate=args.simulate, open_browser=not args.no_browser)


# ------------------------------------------------------------------ check
def cmd_check(args) -> int:
    cfg = _load(args)
    ok = True
    print(f"{APP_NAME} v{__version__} - configuration check ({cfg.config_dir})")
    print(f"  mode={cfg.mode} price_source={cfg.get('system.price_source')} min_rr={cfg.min_rr} "
          f"min_score={cfg.min_score} symbols={cfg.symbols}")
    for w in cfg.warnings:
        print(f"  WARNING: {w}")
    env = PROJECT_ROOT / ".env"
    print(f"  .env: {'found' if env.exists() else 'NOT FOUND (copy .env.example)'}")
    try:
        import MetaTrader5  # noqa: F401
        mt5_ok = True
    except ImportError:
        mt5_ok = False
    print(f"  MetaTrader5 package: {'installed' if mt5_ok else 'not installed (Windows only)'}")
    for a in cfg.accounts:
        line = f"  account {a.name}: kind={a.kind} enabled={a.enabled} mode={a.mode} allow_live={a.allow_live} " \
               f"allow_real={a.allow_real} risk={a.risk_pct}%"
        if a.kind == "mt5" and a.enabled:
            if a.terminal_path and not Path(a.terminal_path).exists():
                line += f"  [terminal not found: {a.terminal_path}]"
            if a.password_env and not a.password():
                line += f"  [{a.password_env} not set in .env]"
        print(line)
    from aurum_nexus.bots.strategist import clamp_profile, default_profile
    import yaml
    for s in cfg.symbols:
        p = cfg.config_dir / "profiles" / f"{s}.yaml"
        prof = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else default_profile(s)
        _, warns = clamp_profile(prof, cfg.min_rr)
        print(f"  profile {s}: {'ok' if not warns else '; '.join(warns)}")
    if args.network:
        from aurum_nexus.data.fallback_adapter import FallbackPublicAdapter
        from aurum_nexus.data.oanda_adapter import OandaAdapter
        fb = FallbackPublicAdapter()
        t = fb.tick(cfg.symbols[0]) if cfg.symbols else None
        print(f"  fallback feed: {'OK ' + str(t.mid) if t else 'unreachable: ' + fb.last_error}")
        o = OandaAdapter()
        print(f"  OANDA: {'connected' if o.connect() else 'n/a (' + o.last_error + ')'}")
    if cfg.mode == "live" and not any(a.allow_live for a in cfg.accounts):
        print("  NOTE: system.mode=live but no account has allow_live=true -> everything stays paper")
    return 0 if ok else 1


# ------------------------------------------------------------------ backtest
def _data(args, symbols):
    from aurum_nexus.backtest.csvio import load_dir, read_m1_csv
    data = {}
    if args.csv_dir:
        data.update(load_dir(args.csv_dir))
    for item in args.csv or []:
        sym, path = item.split("=", 1)
        data[sym.upper()] = read_m1_csv(path)
    if args.dxy_csv:
        data["DXY"] = read_m1_csv(args.dxy_csv)
    missing = [s for s in symbols if s not in data]
    if missing:
        raise SystemExit(f"no CSV for {missing} (use --csv-dir or --csv SYMBOL=path)")
    return data


def cmd_backtest(args) -> int:
    cfg = _load(args)
    symbols = [s.upper() for s in (args.symbols.split(",") if args.symbols else cfg.symbols)]
    data = _data(args, symbols)
    from aurum_nexus.backtest.engine import run_backtest
    from aurum_nexus.backtest.report import write_report
    from aurum_nexus.bots.analyst import stats
    out = Path(args.out or (PROJECT_ROOT / "reports" / f"backtest_{time.strftime('%Y%m%d_%H%M%S')}"))
    kw = dict(start=_ts(args.start), end=_ts(args.end), step=args.step, balance=args.balance,
              warmup_days=args.warmup_days)

    def prog(f, info):
        print(f"\r  {f * 100:5.1f}%  trades {info['trades']}", end="", flush=True)
    print(f"Backtest {symbols} step={args.step}m -> {out}")
    res = run_backtest(cfg, data, symbols, progress=prog, **kw)
    print()
    extra = {}
    if not args.no_tt_grid:
        comp = {"time_targets ON (baseline)": stats(res["trades"])}
        variants = [("time_targets OFF", {"time_targets": {"enabled": False}})]
        for n in (2, 4):
            for t1, t2 in ((1.5, 1.5), (2.5, 3.0)):
                variants.append((f"N={n} T1x{t1} T2x{t2}", {"time_targets": {"reaction_bars": n, "t1_mult": t1,
                                                                              "t2_mult": t2}}))
        for name, ov in variants:
            print(f"  variant: {name}")
            r2 = run_backtest(cfg, data, symbols, overrides=ov, **kw)
            comp[name] = stats(r2["trades"])
        extra["time_target_comparison"] = comp
    summary = write_report(res, cfg, out, extra)
    print(f"Done in {res['runtime_s']}s: {summary['overall']}")
    print(f"Report: {out / 'report.md'}")
    return 0


def cmd_optimize(args) -> int:
    cfg = _load(args)
    sym = args.symbol.upper()
    data = _data(args, [sym])
    from aurum_nexus.backtest.optimizer import walk_forward, write_optimize_report
    from aurum_nexus.bots.strategist import Strategist
    from aurum_nexus.logbus import LogBus
    from aurum_nexus.storage import Storage
    grid = json.loads(args.grid) if args.grid else None
    res = walk_forward(cfg, data, sym, grid, args.train_days, args.test_days, step=args.step)
    st = Storage(cfg.data_dir / "aurum_nexus.db")
    strat = Strategist(LogBus(st), cfg, st)
    strat.load([sym])
    strat.live_mode = cfg.mode == "live"
    out = Path(args.out or (PROJECT_ROOT / "reports" / f"optimize_{sym}_{time.strftime('%Y%m%d_%H%M%S')}"))
    r = write_optimize_report(res, strat if not args.no_register else None, out, register=not args.no_register)
    print(json.dumps({"oos_overall": res["oos_overall"], **r}, indent=2, default=str))
    st.close()
    return 0


def cmd_download(args) -> int:
    cfg = _load(args)
    symbols = [s.upper() for s in (args.symbols.split(",") if args.symbols else cfg.symbols)]
    out = Path(args.out or (PROJECT_ROOT / "data" / "history"))
    from aurum_nexus import history_download as hd
    if args.source == "mt5":
        acc = cfg.account(args.account) if args.account else next((a for a in cfg.accounts if a.kind == "mt5"), None)
        if acc is None:
            raise SystemExit("no MT5 account configured (use --account NAME)")
        paths = hd.download_mt5(acc, symbols, _ts(args.start), _ts(args.end) or time.time(), out,
                                suffixes=cfg.get("symbols.suffix_candidates"))
    elif args.source == "dukascopy":
        paths = hd.download_dukascopy(symbols, _ts(args.start), _ts(args.end) or time.time(), out)
    elif args.source == "oanda":
        paths = hd.download_oanda(symbols, _ts(args.start), _ts(args.end) or time.time(), out)
    else:
        paths = hd.download_yahoo(symbols, out, days=args.days)
    print("written:", *[str(p) for p in paths], sep="\n  ")
    return 0


# ------------------------------------------------------------------ main
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mt5sys", description=f"{APP_NAME} - multi-agent MT5 day-trading system")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    p.add_argument("--config-dir", default=str(PROJECT_ROOT / "config"))
    p.add_argument("--accounts", default=None, help="accounts YAML (default config/accounts.yaml, else demo)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="start hub, account workers and dashboard")
    r.add_argument("--simulate", action="store_true", help="SIMULATED data only (labelled), for demos/tests")
    r.add_argument("--yes-live", action="store_true", help="confirm live trading non-interactively")
    r.add_argument("--no-browser", action="store_true")
    r.add_argument("--price-source", choices=["auto", "mt5", "oanda", "fallback"])
    r.add_argument("--host")
    r.add_argument("--port", type=int)
    r.set_defaults(fn=cmd_run)
    c = sub.add_parser("check", help="validate configuration, floors, terminals, .env")
    c.add_argument("--network", action="store_true", help="also test data sources")
    c.set_defaults(fn=cmd_check)
    for name, fn, h in (("backtest", cmd_backtest, "run the live pipeline on historical M1 CSV"),
                        ("optimize", cmd_optimize, "walk-forward optimisation -> proposed profile")):
        b = sub.add_parser(name, help=h)
        b.add_argument("--csv-dir")
        b.add_argument("--csv", action="append", help="SYMBOL=path.csv (repeatable)")
        b.add_argument("--dxy-csv")
        b.add_argument("--out")
        b.add_argument("--step", type=int, default=1 if name == "backtest" else 5, help="cycle every N M1 bars")
        if name == "backtest":
            b.add_argument("--symbols")
            b.add_argument("--start")
            b.add_argument("--end")
            b.add_argument("--balance", type=float, default=10000.0)
            b.add_argument("--warmup-days", type=float, default=3.0)
            b.add_argument("--no-tt-grid", action="store_true", help="skip time-target comparison runs")
        else:
            b.add_argument("--symbol", required=True)
            b.add_argument("--grid", help='JSON, e.g. {"stops.atr_buffer_mult":[0.25,0.45]}')
            b.add_argument("--train-days", type=float)
            b.add_argument("--test-days", type=float)
            b.add_argument("--no-register", action="store_true", help="do not create a dashboard proposal")
        b.set_defaults(fn=fn)
    d = sub.add_parser("download-history", help="download M1 history to CSV")
    d.add_argument("--source", choices=["mt5", "dukascopy", "oanda", "yahoo"], default="mt5")
    d.add_argument("--symbols")
    d.add_argument("--start", default="2024-01-01")
    d.add_argument("--end")
    d.add_argument("--days", type=int, default=7, help="yahoo only")
    d.add_argument("--account")
    d.add_argument("--out")
    d.set_defaults(fn=cmd_download)
    return p


def main(argv: list[str] | None = None) -> int:
    import multiprocessing
    multiprocessing.freeze_support()   # MT5 worker processes in a frozen Windows build
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
