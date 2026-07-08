#!/usr/bin/env bash
# Launch the trading UI dashboard (Express API + built React SPA) that
# visualizes trading-data/ and can trigger scheduler slots.
#
# First run installs the npm workspace deps and builds the SPA + server;
# later runs skip straight to starting. Serves on 127.0.0.1:${PORT:-4000}.
#
# Usage:
#   scripts/run_ui.sh                 # install/build if needed, then start (foreground)
#   PORT=4100 scripts/run_ui.sh       # override the port
#
# The server binds 127.0.0.1 ONLY — open it from a browser on the box, or
# tunnel in:  ssh -L 4000:127.0.0.1:4000 <host>   then http://localhost:4000
#
# Runs in the FOREGROUND. To keep it up after logout, run under systemd or:
#   setsid nohup scripts/run_ui.sh >/tmp/trading-ui.log 2>&1 &
#
# Requires Node >=18 on PATH.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/../ui"

command -v npm >/dev/null 2>&1 || { echo "run_ui: npm (Node >=18) not found on PATH" >&2; exit 1; }

# First-run init: workspace deps (server + client) then a production build.
if [ ! -d node_modules ]; then
  echo "run_ui: installing npm workspace deps…"
  npm install
fi
if [ ! -d client/dist ] || [ ! -d server/dist ]; then
  echo "run_ui: building SPA + server…"
  npm run build
fi

# Point the UI's python-spawning actions (IB snapshot, analyze-ticker, scheduler
# slots) at the project venv so they get the installed deps (scipy/yfinance/…),
# not the bare system python3.
VENV_PY="$SCRIPT_DIR/../.venv/bin/python"
[ -x "$VENV_PY" ] && export PYTHON_BIN="$VENV_PY"

echo "run_ui: starting dashboard on 127.0.0.1:${PORT:-4000} (PYTHON_BIN=${PYTHON_BIN:-python3})"
exec npm start
