"""Activity log bus. Every decision is logged with its reason.

Tags: SIGNAL, NEW TRADE, CLOSE, BLOCKED, SKIP, MANAGE, EARLY EXIT, LEVEL, ANALYST, SYSTEM, ERROR, SELECTED
Secrets are scrubbed before anything is stored or broadcast.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import deque
from typing import Any, Callable

TAGS = ["SIGNAL", "SELECTED", "NEW TRADE", "CLOSE", "BLOCKED", "SKIP", "MANAGE", "EARLY EXIT", "LEVEL",
        "ANALYST", "STRATEGY", "ACCOUNT", "SYSTEM", "ERROR"]

_log = logging.getLogger("aurum")

SECRET_ENV_HINTS = ("PASSWORD", "TOKEN", "SECRET", "API_KEY")


def _secret_values() -> list[str]:
    vals = []
    for k, v in os.environ.items():
        if v and len(v) >= 4 and any(h in k.upper() for h in SECRET_ENV_HINTS):
            vals.append(v)
    return vals


def scrub(text: str) -> str:
    """Remove any secret env values and obvious credential patterns from text."""
    if not text:
        return text
    for v in _secret_values():
        text = text.replace(v, "***")
    text = re.sub(r"(?i)(password|token|secret|api_key)(['\"]?\s*[:=]\s*['\"]?)([^\s,'\"}]+)", r"\1\2***", text)
    text = re.sub(r"(?i)bearer\s+[a-z0-9\-_.]+", "Bearer ***", text)
    return text


class LogBus:
    def __init__(self, storage=None, maxlen: int = 1000, clock: Callable[[], float] | None = None):
        self.storage = storage
        self.buffer: deque[dict] = deque(maxlen=maxlen)
        self.per_bot: dict[str, deque] = {}
        self.listeners: list[Callable[[dict], None]] = []
        self._lock = threading.Lock()
        self.clock = clock or time.time
        self.persist = True

    def emit(self, tag: str, bot: str, message: str, symbol: str = "", data: dict | None = None) -> dict:
        message = scrub(str(message))
        ev = {"ts": self.clock(), "tag": tag, "bot": bot, "symbol": symbol, "message": message, "data": data or {}}
        with self._lock:
            self.buffer.append(ev)
            self.per_bot.setdefault(bot, deque(maxlen=200)).append(ev)
        if self.storage is not None and self.persist:
            try:
                self.storage.save_event(ev["ts"], tag, bot, symbol, message, data)
            except Exception as e:  # pragma: no cover - storage closed during shutdown
                _log.debug("event persist failed: %s", e)
        _log.info("[%s] %s %s", tag, bot, message)
        for fn in list(self.listeners):
            try:
                fn(ev)
            except Exception:  # pragma: no cover
                pass
        return ev

    def recent(self, n: int = 200, bot: str | None = None) -> list[dict]:
        with self._lock:
            src = self.per_bot.get(bot, deque()) if bot else self.buffer
            return list(src)[-n:]

    def subscribe(self, fn: Callable[[dict], None]) -> None:
        self.listeners.append(fn)


def fmt_r(r: Any) -> str:
    if r is None:
        return "—"
    try:
        return f"{float(r):+.2f}R"
    except (TypeError, ValueError):
        return "—"
