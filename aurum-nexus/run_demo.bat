@echo off
REM Demo: paper accounts from config\accounts.demo.yaml on public PREVIEW data (no MT5 needed).
REM Add --simulate to use clearly labelled SIMULATED prices instead (offline).
cd /d "%~dp0"
if not exist .venv ( python -m venv .venv && .venv\Scripts\pip install -r requirements.txt )
.venv\Scripts\python -m aurum_nexus --accounts config\accounts.demo.yaml run %*
