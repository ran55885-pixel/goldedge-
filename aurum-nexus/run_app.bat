@echo off
REM Desktop app from source: backend + own window + tray icon.
cd /d "%~dp0"
if not exist .venv ( python -m venv .venv && .venv\Scripts\pip install -r requirements.txt )
.venv\Scripts\python -m aurum_nexus.desktop.app %*
