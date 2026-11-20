#!/usr/bin/env bash
# Demo on Linux/macOS: paper accounts on public PREVIEW data (MT5 itself is Windows-only).
# Add --simulate for clearly labelled SIMULATED prices (offline).
set -e
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -r requirements.txt; }
exec .venv/bin/python -m aurum_nexus --accounts config/accounts.demo.yaml run "$@"
