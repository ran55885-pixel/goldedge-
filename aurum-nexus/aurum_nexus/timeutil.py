"""Time, sessions and broker-day helpers. All internal times are epoch seconds UTC.

DST is handled by zoneinfo: sessions are defined in their own local market time zone.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

UTC = timezone.utc


@lru_cache(maxsize=32)
def tz(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def to_dt(ts: float) -> datetime:
    return datetime.fromtimestamp(float(ts), tz=UTC)


def fmt_utc(ts: float | None, with_date: bool = False) -> str:
    if not ts:
        return "n/a"
    return to_dt(ts).strftime("%Y-%m-%d %H:%M:%S UTC" if with_date else "%H:%M:%S UTC")


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def broker_day_start(ts: float, boundary: dict | None = None) -> float:
    """Start (epoch UTC) of the broker trading day containing ts. Default boundary 17:00 New York."""
    boundary = boundary or {"tz": "America/New_York", "time": "17:00"}
    z = tz(boundary["tz"])
    t = _parse_hhmm(boundary["time"])
    local = to_dt(ts).astimezone(z)
    start_local = datetime.combine(local.date(), t, tzinfo=z)
    if local < start_local:
        start_local = datetime.combine(local.date() - timedelta(days=1), t, tzinfo=z)
    return start_local.astimezone(UTC).timestamp()


def _boundary_utc(day_number: int, boundary: dict) -> float:
    """UTC epoch of the boundary local time on calendar date `day_number` (days since 1970-01-01)."""
    z = tz(boundary["tz"])
    t = _parse_hhmm(boundary["time"])
    d = datetime(1970, 1, 1) + timedelta(days=int(day_number))
    return datetime.combine(d.date(), t, tzinfo=z).astimezone(UTC).timestamp()


@lru_cache(maxsize=4096)
def _boundary_cached(day_number: int, tzname: str, hhmm: str) -> float:
    return _boundary_utc(day_number, {"tz": tzname, "time": hhmm})


def broker_day_ids(times: np.ndarray, boundary: dict | None = None) -> np.ndarray:
    """Vectorised trading-day id (int, days since epoch of the day END: a bar after Sunday 17:00 NY belongs
    to Monday) for each bar time. DST-correct via zoneinfo, computed once per calendar date."""
    boundary = boundary or {"tz": "America/New_York", "time": "17:00"}
    t = np.asarray(times, dtype="int64")
    if len(t) == 0:
        return np.zeros(0, dtype="int64")
    # local boundary falls on the same or previous UTC date; test both candidates
    d = t // 86400
    out = d.copy()
    for dd in np.unique(d):
        for cand in (dd - 1, dd, dd + 1):
            b = _boundary_cached(int(cand), boundary["tz"], boundary["time"])
            m = (d == dd) & (t >= b)
            out[m] = cand + 1
    return out


def week_ids(times: np.ndarray, boundary: dict | None = None) -> np.ndarray:
    """Broker week id (Monday-based) from broker day ids."""
    days = broker_day_ids(times, boundary)
    return (days + 3) // 7


def in_window(ts: float, window: dict) -> bool:
    """True if ts is inside a local-time window {tz,start,end}; handles windows crossing midnight."""
    z = tz(window["tz"])
    local = to_dt(ts).astimezone(z).time()
    s = _parse_hhmm(window["start"])
    e = _parse_hhmm(window.get("end", "23:59"))
    if s <= e:
        return s <= local < e
    return local >= s or local < e


def window_bounds(ts: float, window: dict) -> tuple[float, float]:
    """UTC bounds of the most recent occurrence (started at or before ts) of a local window."""
    z = tz(window["tz"])
    local = to_dt(ts).astimezone(z)
    s = _parse_hhmm(window["start"])
    e = _parse_hhmm(window.get("end", window["start"]))
    start = datetime.combine(local.date(), s, tzinfo=z)
    if start > local:
        start -= timedelta(days=1)
    end = datetime.combine(start.date(), e, tzinfo=z)
    if end <= start:
        end += timedelta(days=1)
    return start.astimezone(UTC).timestamp(), end.astimezone(UTC).timestamp()


def current_session(ts: float, sessions: dict) -> str:
    """Primary session label for pacing/playbooks: overlap > london > newyork > asia > off."""
    if in_window(ts, sessions["overlap"]) and in_window(ts, sessions["london"]):
        return "overlap"
    if in_window(ts, sessions["london"]):
        return "london"
    if in_window(ts, sessions["newyork"]):
        return "newyork"
    if in_window(ts, sessions["asia"]):
        return "asia"
    return "off"


def is_weekend(ts: float) -> bool:
    """FX market closed: Friday 17:00 NY -> Sunday 17:00 NY."""
    ny = to_dt(ts).astimezone(tz("America/New_York"))
    wd = ny.weekday()
    if wd == 5:
        return True
    if wd == 4 and ny.time() >= time(17, 0):
        return True
    if wd == 6 and ny.time() < time(17, 0):
        return True
    return False


def minutes_since_week_open(ts: float) -> float:
    ny = to_dt(ts).astimezone(tz("America/New_York"))
    days_back = (ny.weekday() + 1) % 7  # Sunday=0
    open_ = datetime.combine(ny.date() - timedelta(days=days_back), time(17, 0), tzinfo=tz("America/New_York"))
    if open_ > ny:
        open_ -= timedelta(days=7)
    return (ny - open_).total_seconds() / 60.0


def server_to_utc(server_ts: float, offset_hours: float) -> float:
    """MT5 returns server-local wall time encoded as epoch seconds; convert to true UTC."""
    return float(server_ts) - float(offset_hours) * 3600.0


def utc_to_server(ts: float, offset_hours: float) -> float:
    return float(ts) + float(offset_hours) * 3600.0


def tf_floor(ts: float, tf_seconds: int) -> int:
    return int(ts // tf_seconds * tf_seconds)
