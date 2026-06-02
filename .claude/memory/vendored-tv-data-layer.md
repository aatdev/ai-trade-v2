---
name: vendored-tv-data-layer
description: TradingView data layer is now vendored in-repo; no dependency on the external tradingview-mcp-jackson checkout
metadata: 
  node_type: memory
  type: project
  originSessionId: 43992bc0-410c-4f4b-9194-ccc71ac07e39
---

As of 2026-06-02 the repo's TradingView data layer is self-contained — it no longer reads from `/Users/alex/Projects/Repos/tradingview-mcp-jackson`.

- The node `tv` CLI + helper scripts (`tv_earnings_calendar.mjs`, `tv_fundamentals.mjs`, `collect_russell.js`, `read_metrics.js`) are vendored under `vendor/tradingview-mcp/` (mirrors the source layout: `src/cli/index.js`, `scripts/`, `package.json`). Run `npm install` in `vendor/tradingview-mcp/` to (re)populate `node_modules` (gitignored).
- `scripts/lib/tv_client_base.py` and `metrics_cache.py` default `TV_MCP_REPO` to `<repo>/vendor/tradingview-mcp` via `Path(__file__).resolve().parents[2]` (env var still overrides). The old hardcoded `/Users/alex/...` defaults are gone.
- S&P 500 list is committed at `scripts/lib/data/sp500.csv` (the `SP500_CSV` default); the metrics cache + `russel2000.json` live under `vendor/tradingview-mcp/state/` which is gitignored (`state/` pattern), so they're local-only.
- **Why:** make the repo cloneable/independent. Live fetch still needs TradingView Desktop running with CDP on :9222 (`run_tw.sh` launches it); the metrics cache fast-path works fully offline.
- **How to apply:** point new code at `scripts/lib/` (skills add it to `sys.path`); never re-introduce the external repo path. Note CLAUDE.md/README still describe the legacy FMP-based setup and weren't updated.
