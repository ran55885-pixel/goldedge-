"""Create the distributable zip WITHOUT venv, caches, .env, databases, state, logs or downloaded data.

    python tools/make_zip.py --out ../AurumNexus.zip
"""
import argparse
import fnmatch
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOP_EXCLUDE = {".venv", "venv", ".git", "data", "state", "logs", "reports"}       # only at the project root
ANY_EXCLUDE = {"__pycache__", ".pytest_cache", "out", "dist"}
EXCLUDE_FILES = [".env", "*.db", "*.db-journal", "*.db-wal", "*.db-shm", "*.sqlite", "*.pyc", "config/accounts.yaml",
                 "*.log"]


def excluded(rel: Path) -> bool:
    if len(rel.parts) > 1 and rel.parts[0] in TOP_EXCLUDE:
        return True
    if any(part in ANY_EXCLUDE for part in rel.parts[:-1]):
        return True
    s = rel.as_posix()
    return any(fnmatch.fnmatch(s, pat) or fnmatch.fnmatch(rel.name, pat) for pat in EXCLUDE_FILES)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT.parent / "AurumNexus.zip"))
    a = ap.parse_args()
    n = 0
    with zipfile.ZipFile(a.out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(ROOT.rglob("*")):
            rel = p.relative_to(ROOT)
            if p.is_dir() or excluded(rel):
                continue
            z.write(p, Path("aurum-nexus") / rel)
            n += 1
    names = zipfile.ZipFile(a.out).namelist()
    bad = [x for x in names if x.endswith(".env") or x.endswith(".db") or "/.venv/" in x or "__pycache__" in x]
    if bad:
        print("REFUSING: forbidden files in zip:", bad)
        Path(a.out).unlink()
        return 1
    print(f"{a.out}: {n} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
