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

## API surface

Read (each accepts `?date=YYYY-MM-DD`, defaults to the latest available):
`/api/dates`, `/api/exposure`, `/api/watchlist`, `/api/portfolio`, `/api/market`,
`/api/screeners`, `/api/theses` (+ `/:id`), `/api/signals`, `/api/profile`,
`/api/autopilot`, `/api/ticker/:symbol[/:date[/chart/:tf]]`.

Mutation: `DELETE /api/signals/:ticker/:date` removes one signal block from
`analysis/signals.md` in place (same split/rejoin semantics as
`skills/signals-alerts/scripts/prune_signals.mjs`; the preamble and other blocks
are preserved). The Signals Feed panel renders one block per signal with a 🗑
delete button (with confirmation).

Actions (whitelisted, single-job mutex, SSE log stream):
`POST /api/actions/run-slot`, `/sync-alerts`, `/delete-alerts`;
`GET /api/actions/jobs[/:id[/stream]]`.

`run-slot` shells out to `scripts/run_trading_schedule.sh` (which loads `.env`
and respects the scheduler's single-run lock — exit code 75 = busy). It defaults
to `--dry-run` unless explicitly disabled in the UI.

## Notes

- The dashboard is read-mostly: it never mutates `trading-data/`.
- Alert sync/delete require TradingView Desktop running (CDP) and use the
  `skills/signals-alerts/scripts/*.mjs` helpers.
