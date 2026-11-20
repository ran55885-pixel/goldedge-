"""Bot base class: one job, a status string, a log and counters shown on the dashboard."""
from __future__ import annotations

import time
from collections import deque


class Bot:
    name = "BOT"
    color = "#00e5ff"
    job = ""

    def __init__(self, bus, cfg, clock=None):
        self.bus = bus
        self.cfg = cfg
        self.clock = clock or time.time
        self.status = "idle"
        self.status_ts = 0.0
        self.counters: dict[str, int] = {}
        self.activity: deque = deque(maxlen=60)   # (ts, value) for the desk mini-chart

    def set_status(self, s: str) -> None:
        self.status = s
        self.status_ts = self.clock()

    def log(self, tag: str, message: str, symbol: str = "", data: dict | None = None) -> dict:
        self.count(tag)
        return self.bus.emit(tag, self.name, message, symbol, data)

    def count(self, key: str, n: int = 1) -> None:
        self.counters[key] = self.counters.get(key, 0) + n

    def pulse(self, value: float) -> None:
        self.activity.append((self.clock(), float(value)))

    def config_view(self) -> dict:
        return {}

    def snapshot(self) -> dict:
        return {"name": self.name, "color": self.color, "job": self.job, "status": self.status,
                "status_ts": self.status_ts, "counters": dict(self.counters),
                "activity": [v for _, v in self.activity], "config": self.config_view()}
