#!/bin/bash
# Thin launcher for launchd -> run_trading_schedule.py
#
# Usage:
#   bash scripts/run_trading_schedule.sh --slot premarket
#   bash scripts/run_trading_schedule.sh --slot evening-prep
#   bash scripts/run_trading_schedule.sh --slot monthly
#
# Install the launchd agents (CET == machine local time for a CET-based trader):
#   for s in premarket evening-prep monthly; do
#     sed "s|\$HOME|$HOME|g; s|\$PROJECT_DIR|$(pwd)|g" \
#       "launchd/com.trade-analysis.trading-$s.plist" \
#       > "$HOME/Library/LaunchAgents/com.trade-analysis.trading-$s.plist"
#     launchctl load "$HOME/Library/LaunchAgents/com.trade-analysis.trading-$s.plist"
#   done
#   launchctl list | grep trading-
#
# Manual dry-run test:
#   bash scripts/run_trading_schedule.sh --slot evening-prep --dry-run

export TV_NO_CACHE="1"

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:${HOME}/.local/bin:/usr/local/bin:$PATH"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.." || exit 1

command -v python3 >/dev/null 2>&1 || { echo "python3 not found" >&2; exit 1; }

# Prefer the project virtualenv: it has the declared deps (pyyaml, pandas, ...)
# that bare Homebrew/system python3 lacks. Skill scripts run via sys.executable,
# so the whole process tree inherits whichever interpreter starts here.
if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
else
    PYTHON="python3"
fi

# Load secrets (FMP / ALPACA / TELEGRAM) and any project env. Both files are
# gitignored. .env uses `export VAR=...`; .envrc is direnv-managed.
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi
if command -v direnv >/dev/null 2>&1 && [ -f .envrc ]; then
    eval "$(direnv export bash 2>/dev/null)"
fi

"${PYTHON}" scripts/run_trading_schedule.py "$@"
