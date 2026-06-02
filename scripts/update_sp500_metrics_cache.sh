#!/usr/bin/env bash
#
# Refresh the per-ticker metrics cache for the S&P 500 universe.
#
# Reads  vendor/tradingview-mcp/state/sp500.csv
# Writes vendor/tradingview-mcp/state/metrics/<TICKER>/  (incremental: --update
#        fetches only the missing days and merges into the stored series).
#
# Requires TradingView Desktop running with Chrome DevTools Protocol on :9222
# (launch it with ./run_tw.sh or vendor/tradingview-mcp/scripts/launch_tv_debug_mac.sh).
#
# Any extra arguments are forwarded to collect_russell.js (e.g. --limit 50).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TV_DIR="$SCRIPT_DIR/../vendor/tradingview-mcp"

cd "$TV_DIR"
exec node scripts/collect_russell.js --source snp500 --update "$@"
