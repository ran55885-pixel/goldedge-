"""Build guards: no moving averages anywhere in strategy or chart code; no hard-coded price levels."""
import io
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "aurum_nexus"

MA_PATTERNS = [
    r"\b(sma|ema|wma|hma|dema|tema|smma|lwma|vwma|kama|rma|zlema|alma|trima|mcginley)\b",
    r"moving[\s_\-]*average",
    r"\.rolling\(",
    r"\.ewm\(",
    r"\bMA\(",
    r"\bma_(period|length|len|fast|slow|type)\b",
    r"\bSimpleMovingAverage\b|\bExponentialMovingAverage\b",
]


def code_files():
    for p in list(PKG.rglob("*.py")) + list((PKG / "web").rglob("*.js")) + list((PKG / "web").rglob("*.html")):
        if "vendor" in p.parts:
            continue
        yield p


def test_no_moving_averages_anywhere():
    bad = []
    for p in code_files():
        text = p.read_text(encoding="utf-8", errors="ignore")
        for pat in MA_PATTERNS:
            for m in re.finditer(pat, text, flags=re.IGNORECASE):
                line = text[:m.start()].count("\n") + 1
                bad.append(f"{p.relative_to(ROOT)}:{line}: {m.group(0)}")
    assert not bad, "moving-average usage found (forbidden):\n" + "\n".join(bad)


def test_guard_detects_a_planted_ma(tmp_path):
    sample = "x = close.rolling(20).mean()  # sma\n"
    assert any(re.search(p, sample, re.IGNORECASE) for p in MA_PATTERNS)


# ------------------------------------------------------------------ no hard-coded prices
STRATEGY_FILES = ["indicators", "bots", "coordinator.py", "engine.py", "context.py", "tradebook.py", "paper.py"]
ALLOWED_FLOATS = {100.0, 10.0, 20.0, 30.0, 60.0, 1000.0, 3600.0, 86400.0, 1e9, 1e18, 1e-12, 1e-9, 1e-6}


def strategy_py():
    for name in STRATEGY_FILES:
        p = PKG / name
        yield from ([p] if p.is_file() else p.rglob("*.py"))


def suspicious_numbers(src: str):
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type != tokenize.NUMBER:
            continue
        s = tok.string.replace("_", "")
        if not any(ch in s for ch in ".eE") or s.lower().startswith("0x"):
            continue
        try:
            v = abs(float(s))
        except ValueError:
            continue
        if v in ALLOWED_FLOATS:
            continue
        decimals = len(s.split(".")[1]) if "." in s and "e" not in s.lower() else 0
        if v >= 10.0 or decimals >= 4:
            out.append((tok.start[0], s))
    return out


def test_no_hard_coded_price_levels():
    bad = []
    for p in strategy_py():
        for line, s in suspicious_numbers(p.read_text(encoding="utf-8")):
            bad.append(f"{p.relative_to(ROOT)}:{line}: {s}")
    assert not bad, "price-like literals in strategy code (levels must come from data/config):\n" + "\n".join(bad)


def test_price_guard_detects_planted_level():
    assert suspicious_numbers("if price > 2400.0: buy()\n")
    assert suspicious_numbers("lvl = 1.08500\n")
    assert not suspicious_numbers("x = 0.5 * atr\n")


def test_no_secrets_in_repo_files():
    pat = re.compile(r"(?i)(password|token|secret)\s*[:=]\s*['\"][^'\"\s]{6,}['\"]")
    bad = []
    for p in list(PKG.rglob("*.py")) + list((ROOT / "config").rglob("*.yaml")):
        for m in pat.finditer(p.read_text(encoding="utf-8", errors="ignore")):
            bad.append(f"{p.relative_to(ROOT)}: {m.group(0)[:40]}")
    assert not bad, bad
