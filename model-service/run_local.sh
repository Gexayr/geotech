#!/usr/bin/env sh
# One-click local start on Linux / macOS: creates .venv on first run, then serves http://localhost:8000
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3.10 -m venv .venv || python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python app/server.py --port 8000
