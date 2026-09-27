@echo off
rem One-click local start on Windows: creates .venv on first run, then serves http://localhost:8000
cd /d %~dp0
if not exist .venv (
  py -3.10 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install -r requirements.txt
)
.venv\Scripts\python app\server.py --port 8000
