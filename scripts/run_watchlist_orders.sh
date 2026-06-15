#!/bin/bash
# Thin launcher for launchd / manual runs -> watchlist_orders.py
#
# Usage:
#   bash scripts/run_watchlist_orders.sh send                 # rs cards (producer)
#   bash scripts/run_watchlist_orders.sh listen --live        # confirmation daemon
#
# Install the daemon launchd agent (CET == machine local time):
#   sed "s|\$HOME|$HOME|g; s|\$PROJECT_DIR|$(pwd)|g" \
#     launchd/com.trade-analysis.watchlist-order-daemon.plist \
#     > "$HOME/Library/LaunchAgents/com.trade-analysis.watchlist-order-daemon.plist"
#   launchctl load "$HOME/Library/LaunchAgents/com.trade-analysis.watchlist-order-daemon.plist"
#
# Real order placement also requires IB_ALLOW_ORDER_PLACEMENT=true in .env AND
# the --live flag; without both the daemon runs in preview (places nothing).

export TV_NO_CACHE="1"

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:${HOME}/.local/bin:/usr/local/bin:$PATH"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.." || exit 1

command -v python3 >/dev/null 2>&1 || { echo "python3 not found" >&2; exit 1; }

# Prefer the project virtualenv (declared deps live there; bare python3 lacks them).
if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
else
    PYTHON="python3"
fi

# Load secrets (IB / TELEGRAM) and any project env. Both files are gitignored.
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi
if command -v direnv >/dev/null 2>&1 && [ -f .envrc ]; then
    eval "$(direnv export bash 2>/dev/null)"
fi

"${PYTHON}" scripts/watchlist_orders.py "$@"
