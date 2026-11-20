"""SQLite persistence: signals, decisions, trades, fills/slippage, equity snapshots, profile changes,
activity log, shadow positions, users, proposals and key/value state. Thread-safe via a lock."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
  id TEXT PRIMARY KEY, ts REAL, symbol TEXT, timeframe TEXT, setup TEXT, direction TEXT,
  entry REAL, stop REAL, tp1 REAL, tp2 REAL, score REAL, base_score REAL, components TEXT, modifiers TEXT,
  confirmations TEXT, session TEXT, regime TEXT, rr_final REAL
);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, signal_id TEXT, account TEXT, symbol TEXT,
  decision TEXT, reason TEXT, score REAL
);
CREATE TABLE IF NOT EXISTS trades (
  id TEXT PRIMARY KEY, account TEXT, mode TEXT, symbol TEXT, direction TEXT, setup TEXT, timeframe TEXT,
  session TEXT, regime TEXT, signal_id TEXT, score REAL, open_ts REAL, close_ts REAL, entry REAL, stop REAL,
  initial_stop REAL, tp1 REAL, tp2 REAL, volume REAL, volume_open REAL, exit_price REAL, pnl REAL, r_multiple REAL,
  mfe_r REAL, mae_r REAL, exit_type TEXT, exit_reason TEXT, status TEXT, ticket INTEGER, risk_money REAL,
  tp1_done INTEGER DEFAULT 0, meta TEXT
);
CREATE TABLE IF NOT EXISTS fills (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, trade_id TEXT, account TEXT, symbol TEXT, side TEXT,
  volume REAL, requested REAL, price REAL, slippage REAL, retcode INTEGER, attempts INTEGER, filling TEXT, kind TEXT
);
CREATE TABLE IF NOT EXISTS equity (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, account TEXT, currency TEXT, balance REAL, equity REAL,
  source TEXT
);
CREATE INDEX IF NOT EXISTS ix_equity_acc_ts ON equity(account, ts);
CREATE TABLE IF NOT EXISTS profile_changes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, symbol TEXT, version INTEGER, change TEXT, reason TEXT, source TEXT
);
CREATE TABLE IF NOT EXISTS proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, symbol TEXT, changes TEXT, reason TEXT, source TEXT,
  status TEXT, report TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, tag TEXT, bot TEXT, symbol TEXT, message TEXT, data TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS shadows (
  id TEXT PRIMARY KEY, trade_id TEXT, ts REAL, symbol TEXT, direction TEXT, entry REAL, stop REAL, tp REAL,
  risk REAL, exit_r REAL, shadow_r REAL, status TEXT, closed_ts REAL, rule TEXT
);
CREATE TABLE IF NOT EXISTS users (
  username TEXT PRIMARY KEY, pw_hash TEXT, created REAL
);
CREATE TABLE IF NOT EXISTS kv (
  k TEXT PRIMARY KEY, v TEXT, ts REAL
);
"""


class Storage:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            if self.path != ":memory:":
                self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    # ---------------------------------------------------------------- generic
    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def query(self, sql: str, params: tuple | list = ()) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            try:
                self.conn.commit()
                self.conn.close()
            except sqlite3.ProgrammingError:
                pass

    # ---------------------------------------------------------------- signals / decisions
    def save_signal(self, c: Any) -> None:
        self.execute(
            "INSERT OR REPLACE INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (c.id, c.created, c.symbol, c.timeframe, c.setup, c.direction, c.entry, c.stop, c.tp1, c.tp2,
             c.score, c.base_score, json.dumps(c.components), json.dumps(c.modifiers),
             json.dumps(c.confirmations), c.session, c.regime, c.rr_final),
        )

    def save_decision(self, signal_id: str, account: str, symbol: str, decision: str, reason: str,
                      score: float, ts: float | None = None) -> None:
        self.execute("INSERT INTO decisions (ts,signal_id,account,symbol,decision,reason,score) VALUES (?,?,?,?,?,?,?)",
                     (ts or time.time(), signal_id, account, symbol, decision, reason, score))

    def decisions(self, limit: int = 200) -> list[dict]:
        return self.query("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,))

    # ---------------------------------------------------------------- trades
    TRADE_FIELDS = ["id", "account", "mode", "symbol", "direction", "setup", "timeframe", "session", "regime",
                    "signal_id", "score", "open_ts", "close_ts", "entry", "stop", "initial_stop", "tp1", "tp2",
                    "volume", "volume_open", "exit_price", "pnl", "r_multiple", "mfe_r", "mae_r", "exit_type",
                    "exit_reason", "status", "ticket", "risk_money", "tp1_done", "meta"]

    def upsert_trade(self, t: dict) -> None:
        row = {k: t.get(k) for k in self.TRADE_FIELDS}
        if isinstance(row.get("meta"), (dict, list)):
            row["meta"] = json.dumps(row["meta"])
        cols = ",".join(self.TRADE_FIELDS)
        qs = ",".join("?" for _ in self.TRADE_FIELDS)
        self.execute(f"INSERT OR REPLACE INTO trades ({cols}) VALUES ({qs})", [row[k] for k in self.TRADE_FIELDS])

    def trades(self, status: str | None = None, account: str | None = None, limit: int = 5000,
               mode: str | None = None) -> list[dict]:
        sql = "SELECT * FROM trades WHERE 1=1"
        p: list = []
        if status:
            sql += " AND status=?"
            p.append(status)
        if account:
            sql += " AND account=?"
            p.append(account)
        if mode:
            sql += " AND mode=?"
            p.append(mode)
        sql += " ORDER BY open_ts DESC LIMIT ?"
        p.append(limit)
        rows = self.query(sql, p)
        for r in rows:
            if r.get("meta"):
                try:
                    r["meta"] = json.loads(r["meta"])
                except (TypeError, ValueError):
                    pass
        return rows

    def save_fill(self, trade_id: str, account: str, symbol: str, side: str, volume: float, requested: float,
                  price: float, slippage: float, retcode: int, attempts: int, filling: str, kind: str) -> None:
        self.execute("INSERT INTO fills (ts,trade_id,account,symbol,side,volume,requested,price,slippage,retcode,"
                     "attempts,filling,kind) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (time.time(), trade_id, account, symbol, side, volume, requested, price, slippage, retcode,
                      attempts, filling, kind))

    # ---------------------------------------------------------------- equity
    def save_equity(self, account: str, currency: str, balance: float | None, equity: float | None,
                    source: str, ts: float | None = None) -> None:
        if balance is None and equity is None:
            return
        self.execute("INSERT INTO equity (ts,account,currency,balance,equity,source) VALUES (?,?,?,?,?,?)",
                     (ts or time.time(), account, currency, balance, equity, source))

    def equity_history(self, account: str | None = None, since: float = 0, max_points: int = 600) -> list[dict]:
        if account:
            rows = self.query("SELECT ts,account,currency,balance,equity,source FROM equity WHERE account=? AND ts>=? "
                              "ORDER BY ts", (account, since))
        else:
            rows = self.query("SELECT ts,account,currency,balance,equity,source FROM equity WHERE ts>=? ORDER BY ts",
                              (since,))
        if len(rows) > max_points:
            step = len(rows) / max_points
            rows = [rows[int(i * step)] for i in range(max_points)] + [rows[-1]]
        return rows

    # ---------------------------------------------------------------- events
    def save_event(self, ts: float, tag: str, bot: str, symbol: str, message: str, data: dict | None = None) -> None:
        self.execute("INSERT INTO events (ts,tag,bot,symbol,message,data) VALUES (?,?,?,?,?,?)",
                     (ts, tag, bot, symbol, message, json.dumps(data or {}, default=str)))

    def events(self, limit: int = 200, bot: str | None = None) -> list[dict]:
        if bot:
            return self.query("SELECT * FROM events WHERE bot=? ORDER BY id DESC LIMIT ?", (bot, limit))
        return self.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))

    # ---------------------------------------------------------------- profiles / proposals
    def save_profile_change(self, symbol: str, version: int, change: dict, reason: str, source: str) -> None:
        self.execute("INSERT INTO profile_changes (ts,symbol,version,change,reason,source) VALUES (?,?,?,?,?,?)",
                     (time.time(), symbol, version, json.dumps(change, default=str), reason, source))

    def add_proposal(self, symbol: str, changes: dict, reason: str, source: str, report: dict | None = None) -> int:
        cur = self.execute("INSERT INTO proposals (ts,symbol,changes,reason,source,status,report) VALUES (?,?,?,?,?,?,?)",
                           (time.time(), symbol, json.dumps(changes, default=str), reason, source, "pending",
                            json.dumps(report or {}, default=str)))
        return int(cur.lastrowid)

    def proposals(self, status: str | None = "pending", symbol: str | None = None) -> list[dict]:
        sql = "SELECT * FROM proposals WHERE 1=1"
        p: list = []
        if status:
            sql += " AND status=?"
            p.append(status)
        if symbol:
            sql += " AND symbol=?"
            p.append(symbol)
        rows = self.query(sql + " ORDER BY id DESC", p)
        for r in rows:
            r["changes"] = json.loads(r["changes"] or "{}")
            r["report"] = json.loads(r["report"] or "{}")
        return rows

    def set_proposal_status(self, pid: int, status: str) -> None:
        self.execute("UPDATE proposals SET status=? WHERE id=?", (status, pid))

    # ---------------------------------------------------------------- shadows
    def save_shadow(self, s: dict) -> None:
        cols = ["id", "trade_id", "ts", "symbol", "direction", "entry", "stop", "tp", "risk", "exit_r", "shadow_r",
                "status", "closed_ts", "rule"]
        self.execute(f"INSERT OR REPLACE INTO shadows ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                     [s.get(c) for c in cols])

    def shadows(self, status: str | None = None) -> list[dict]:
        if status:
            return self.query("SELECT * FROM shadows WHERE status=? ORDER BY ts DESC", (status,))
        return self.query("SELECT * FROM shadows ORDER BY ts DESC")

    # ---------------------------------------------------------------- users
    def get_user(self, username: str) -> dict | None:
        rows = self.query("SELECT * FROM users WHERE username=?", (username,))
        return rows[0] if rows else None

    def user_count(self) -> int:
        return int(self.query("SELECT COUNT(*) AS n FROM users")[0]["n"])

    def create_user(self, username: str, pw_hash: str) -> None:
        self.execute("INSERT INTO users (username,pw_hash,created) VALUES (?,?,?)", (username, pw_hash, time.time()))

    # ---------------------------------------------------------------- kv
    def kv_set(self, k: str, v: Any) -> None:
        self.execute("INSERT OR REPLACE INTO kv (k,v,ts) VALUES (?,?,?)", (k, json.dumps(v, default=str), time.time()))

    def kv_get(self, k: str, default: Any = None) -> Any:
        rows = self.query("SELECT v FROM kv WHERE k=?", (k,))
        if not rows:
            return default
        try:
            return json.loads(rows[0]["v"])
        except (TypeError, ValueError):
            return default
