# Trading State Dashboard (`ui/`)

Local web dashboard that visualizes the state produced by
`scripts/run_trading_schedule.py` and the skill scripts it invokes — the files
under `trading-data/` (`schedule/`, `market/`, `screeners/`, `journal/`,
`analysis/`, `logs/`). It also exposes buttons to run scheduler slots and sync
TradingView alerts.

- **client/** — React + TypeScript (Vite) single-page app
- **server/** — Express + TypeScript API that reads `trading-data/` and spawns
  whitelisted scheduler/alert commands
- **shared/** — type-only API contract imported by both

The server binds to `127.0.0.1` only — it can spawn real scheduler processes.

## Quick start

```bash
cd ui
npm install            # installs both workspaces
npm run dev            # server on :4000, client on :5173 (proxies /api)
# open http://localhost:5173
```

Production (single port, serves the built SPA + API):

```bash
cd ui
npm run build
npm start              # http://127.0.0.1:4000
```

Tests / type-check:

```bash
npm test               # vitest (server: file resolution, route mapping, action guards)
npm run typecheck
```

## Configuration (`ui/.env`, all optional — see `.env.example`)

| Var | Default | Meaning |
|-----|---------|---------|
| `PORT` | `4000` | API / prod server port |
| `TRADING_DATE_DIR` | `trading-data` | Trading-data dir (mirrors the scheduler env var; note: `DATE`) |
| `TRADING_PROJECT_ROOT` | auto-detected | Repo root (walks up to `scripts/run_trading_schedule.py`) |
| `TRADING_UI_ANALYZE_MODEL` | `claude-opus-4-8` | Model for `analyze-ticker` headless runs |
| `TRADING_UI_MCP_CONFIG` | vendored TradingView server | `--mcp-config` file for `analyze-ticker` (point at a custom MCP config) |

## API surface

Read (each accepts `?date=YYYY-MM-DD`, defaults to the latest available):
`/api/dates`, `/api/exposure`, `/api/watchlist`, `/api/portfolio`, `/api/market`,
`/api/screeners`, `/api/theses` (+ `/:id`), `/api/signals`, `/api/profile`,
`/api/autopilot`, `/api/analysis/tickers` (which tickers already have saved
analysis), `/api/ticker/:symbol[/:date[/chart/:tf]]`.

Mutation: `DELETE /api/signals/:ticker/:date` removes one signal block from
`analysis/signals.md` in place (same split/rejoin semantics as
`skills/signals-alerts/scripts/prune_signals.mjs`; the preamble and other blocks
are preserved). The Signals Feed panel is a compact table (Date / Ticker /
Signal) with a **ticker filter**; clicking a row opens a modal with the full
rendered block, and each row / the modal has a 🗑 delete button (with
confirmation).

Actions (whitelisted, single-job mutex, SSE log stream):
`POST /api/actions/run-slot`, `/sync-alerts`, `/delete-alerts`,
`/analyze-ticker` (runs the `ticker-analysis` skill via headless
`claude -p … --output-format stream-json`);
`GET /api/actions/jobs[/:id[/stream]]`, `POST /api/actions/jobs/:id/cancel`.

### Run ticker analysis from the Watchlist

Each watchlist row shows a 📄 flag next to tickers that already have saved
analysis (linking to the ticker page), and a **🔍 Analyze** button that opens a
modal with two options before running:

- **Create TradingView alerts** — after the analysis, create the priority-scenario
  alerts (Trigger / Stop / T1 / T2 / T3) via the `signals-alerts` skill.
- **Save to Notes** — also save the final report to MyNotes via the `save-note`
  skill (subfolder `Анализ-тикеров/<TICKER>`).

Progress then streams live (each tool/skill step is summarized from the claude
stream-json events) inline in the row and in the modal; a running analysis can
be cancelled. On completion the analysis
flag and the ticker page refresh automatically. Output lands in
`trading-data/analysis/<TICKER>/<date>/` (four markdown docs + daily/weekly
screenshots); no TradingView alerts are created.

The run uses `claude -p` on **Opus 4.8** (`claude-opus-4-8`,
override via `TRADING_UI_ANALYZE_MODEL`) and loads the vendored TradingView MCP
server via `--mcp-config --strict-mcp-config` so the skill has the
`mcp__tradingview__*` tools. **Prerequisite:** TradingView Desktop must be
running with Chrome DevTools Protocol on `:9222` (launch via `./run_tw.sh`); the
MCP server connects to it lazily on the first tool call. Point
`TRADING_UI_MCP_CONFIG` at a different MCP config file to use another server.

`run-slot` shells out to `scripts/run_trading_schedule.sh` (which loads `.env`
and respects the scheduler's single-run lock — exit code 75 = busy). It defaults
to `--dry-run` unless explicitly disabled in the UI.

## Notes

- The dashboard is read-mostly: it never mutates `trading-data/`.
- Alert sync/delete require TradingView Desktop running (CDP) and use the
  `skills/signals-alerts/scripts/*.mjs` helpers.
