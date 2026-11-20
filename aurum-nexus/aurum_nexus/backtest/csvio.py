"""Backtest CSV I/O (format documented in docs/BACKTEST_CSV.md).

Required columns: time, open, high, low, close. Optional: tick_volume, real_volume, spread (points).
time: UTC, either 'YYYY-MM-DD HH:MM:SS' / ISO-8601 or epoch seconds. One M1 file per symbol.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from aurum_nexus.data.bars import normalize
from aurum_nexus.symbols import canonical

REQUIRED = ["time", "open", "high", "low", "close"]


def read_m1_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path, comment="#")
    df.columns = [c.strip().lower() for c in df.columns]
    aliases = {"timestamp": "time", "datetime": "time", "date": "time", "volume": "tick_volume", "vol": "tick_volume"}
    df = df.rename(columns={k: v for k, v in aliases.items() if k in df.columns and v not in df.columns})
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing columns {missing} (see docs/BACKTEST_CSV.md)")
    t = df["time"]
    if pd.api.types.is_numeric_dtype(t):
        secs = t.astype("int64")
        if secs.max() > 10 ** 11:          # milliseconds
            secs = secs // 1000
    else:
        secs = pd.to_datetime(t, utc=True).dt.as_unit("s").astype("int64")
    df["time"] = secs
    for c in ("tick_volume", "real_volume", "spread"):
        if c not in df.columns:
            df[c] = 0.0
    out = normalize(df)
    bad = out[(out["high"] < out[["open", "close"]].max(axis=1)) | (out["low"] > out[["open", "close"]].min(axis=1))]
    if len(bad):
        raise ValueError(f"{path}: {len(bad)} rows with high/low inconsistent with open/close")
    return out


def load_dir(csv_dir: str | Path, symbols: list[str] | None = None) -> dict[str, pd.DataFrame]:
    out = {}
    for p in sorted(Path(csv_dir).glob("*.csv")):
        sym = canonical(p.stem.split("_")[0]) if p.stem.upper() not in ("DXY",) else "DXY"
        if p.stem.upper().startswith("DXY"):
            sym = "DXY"
        if symbols and sym not in symbols and sym != "DXY":
            continue
        out[sym] = read_m1_csv(p)
    return out


def write_m1_csv(df: pd.DataFrame, path: str | Path, header_comment: str | None = None) -> None:
    out = df.copy()
    out["time"] = pd.to_datetime(out["time"], unit="s", utc=True).dt.strftime("%Y-%m-%d %H:%M:%S")
    cols = ["time", "open", "high", "low", "close", "tick_volume", "real_volume", "spread"]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        if header_comment:
            for line in header_comment.splitlines():
                f.write(f"# {line}\n")
        out[cols].to_csv(f, index=False)
