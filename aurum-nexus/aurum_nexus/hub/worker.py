"""Account worker processes.

The MetaTrader5 package supports ONE terminal per process, so every MT5 account runs in its own worker
process bound to its own portable terminal installation. The hub talks to it through a request/response
queue pair; WorkerProxy exposes the same adapter interface as Mt5Adapter.
For tests the proxy can run an adapter inline (same process, no queues).
"""
from __future__ import annotations

import itertools
import multiprocessing as mp
import queue
import threading
import time

from aurum_nexus.data.base import TradingAdapter

CALLS = {"connect", "disconnect", "ping", "resolve_symbol", "symbol_spec", "tick", "bars", "bars_range", "account",
         "positions", "deals", "market_order", "close_position", "modify_sltp", "market_book", "has_volume", "status"}


def worker_main(acc_dict: dict, cmd_q, res_q, suffixes: list, default_offset: float, magic: int) -> None:  # pragma: no cover - runs on Windows
    from aurum_nexus.config import AccountConfig, load_env
    from aurum_nexus.data.mt5_adapter import Mt5Adapter
    load_env(__import__("pathlib").Path(__file__).resolve().parents[2] / "config")
    acc = AccountConfig(**acc_dict)
    ad = Mt5Adapter(acc, suffix_candidates=suffixes, default_offset_hours=default_offset, bot_magic=magic)
    while True:
        try:
            item = cmd_q.get()
        except (EOFError, KeyboardInterrupt):
            break
        if item is None:
            break
        rid, method, args = item
        try:
            if method not in CALLS:
                raise ValueError(f"method {method} not allowed")
            res = getattr(ad, method)(*args)
            res_q.put((rid, True, res, ad.connected, ad.last_error))
        except Exception as e:
            res_q.put((rid, False, f"{type(e).__name__}: {e}", ad.connected, ad.last_error))
    try:
        ad.disconnect()
    except Exception:
        pass


class WorkerProxy(TradingAdapter):
    kind = "mt5"
    is_preview = False

    def __init__(self, acc_cfg, suffixes=None, default_offset: float = 2.0, magic: int = 770077,
                 inline_adapter: TradingAdapter | None = None, timeout: float = 15.0):
        super().__init__()
        self.acc = acc_cfg
        self.account_name = acc_cfg.name
        self.label = f"MT5 · {acc_cfg.name}"
        self.timeout = timeout
        self.inline = inline_adapter
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self.proc = None
        self.suffixes = suffixes or [""]
        self.default_offset = default_offset
        self.magic = magic
        if inline_adapter is None:
            ctx = mp.get_context("spawn")
            self.cmd_q = ctx.Queue()
            self.res_q = ctx.Queue()
            self._ctx = ctx

    def start(self) -> None:
        if self.inline is not None or (self.proc is not None and self.proc.is_alive()):
            return
        acc_dict = {k: v for k, v in self.acc.__dict__.items()}
        self.proc = self._ctx.Process(target=worker_main, name=f"mt5-{self.acc.name}", daemon=True,
                                      args=(acc_dict, self.cmd_q, self.res_q, self.suffixes, self.default_offset,
                                            self.magic))
        self.proc.start()

    def stop(self) -> None:
        if self.inline is not None:
            try:
                self.inline.disconnect()
            except Exception:
                pass
            return
        if self.proc is not None:
            try:
                self.cmd_q.put(None)
                self.proc.join(timeout=5)
            finally:
                if self.proc.is_alive():
                    self.proc.terminate()

    def _call(self, method: str, *args):
        if self.inline is not None:
            try:
                res = getattr(self.inline, method)(*args)
            finally:
                self.connected = bool(self.inline.connected)
                self.last_error = self.inline.last_error
            return res
        if self.proc is None or not self.proc.is_alive():
            self.start()
        with self._lock:
            rid = next(self._ids)
            self.cmd_q.put((rid, method, args))
            deadline = time.time() + self.timeout
            while True:
                left = deadline - time.time()
                if left <= 0:
                    self.connected = False
                    self.last_error = f"worker timeout on {method}"
                    raise TimeoutError(self.last_error)
                try:
                    r = self.res_q.get(timeout=left)
                except queue.Empty:
                    continue
                if r[0] != rid:
                    continue  # stale reply of a timed-out call
                _, ok, res, conn, err = r
                self.connected, self.last_error = bool(conn), err
                if not ok:
                    raise RuntimeError(res)
                return res

    # adapter interface -------------------------------------------------------------------------
    def connect(self) -> bool:
        return bool(self._call("connect"))

    def disconnect(self) -> None:
        try:
            self._call("disconnect")
        except Exception:
            pass
        self.connected = False

    def ping(self) -> bool:
        return bool(self._call("ping"))

    def resolve_symbol(self, canonical):
        return self._call("resolve_symbol", canonical)

    def symbol_spec(self, canonical):
        return self._call("symbol_spec", canonical)

    def tick(self, canonical):
        return self._call("tick", canonical)

    def bars(self, canonical, tf, count):
        return self._call("bars", canonical, tf, count)

    def bars_range(self, canonical, tf, start, end):
        return self._call("bars_range", canonical, tf, start, end)

    def account(self):
        return self._call("account")

    def positions(self):
        return self._call("positions")

    def deals(self, since_utc, until_utc):
        return self._call("deals", since_utc, until_utc)

    def market_order(self, *a):
        return self._call("market_order", *a)

    def close_position(self, *a, **kw):
        return self._call("close_position", *a)

    def modify_sltp(self, *a):
        return self._call("modify_sltp", *a)

    def market_book(self, canonical):
        return self._call("market_book", canonical)

    def has_volume(self, canonical):
        return True
