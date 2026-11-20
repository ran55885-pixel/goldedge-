@echo off
REM Hub + dashboard in the browser (accounts from config\accounts.yaml; paper unless live is fully enabled).
cd /d "%~dp0"
if not exist .venv ( python -m venv .venv && .venv\Scripts\pip install -r requirements.txt )
.venv\Scripts\python -m aurum_nexus run %*
