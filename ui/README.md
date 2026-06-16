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
| `UI_AUTH_USER` | _unset_ | Login username. Set together with `UI_AUTH_PASSWORD` to require login. |
| `UI_AUTH_PASSWORD` | _unset_ | Login password. Auth is **disabled** unless both user + password are set. |
| `UI_AUTH_SECRET` | derived from creds | Signing key for the session cookie. If unset, derived from the credentials (so changing the password invalidates outstanding sessions). |
| `UI_AUTH_TTL_HOURS` | `168` | Session lifetime in hours (default 7 days). |
| `TRADING_DATE_DIR` | `trading-data` | Trading-data dir (mirrors the scheduler env var; note: `DATE`) |
| `TRADING_PROJECT_ROOT` | auto-detected | Repo root (walks up to `scripts/run_trading_schedule.py`) |
| `TRADING_UI_ANALYZE_MODEL` | `claude-opus-4-8` | Model for `analyze-ticker` headless runs |
| `TRADING_UI_MCP_CONFIG` | vendored TradingView server | `--mcp-config` file for `analyze-ticker` (point at a custom MCP config) |
| `PYTHON_BIN` | `python3` | Interpreter used to shell out to the IB snapshot script |
| `TRADING_UI_IB_FIXTURE` | _unset_ | Path to a recorded IB snapshot JSON; when set, `/api/ib` reads it instead of contacting the Gateway (offline dev / demo) |
| `TRADING_UI_TV_BIN` | `tv` | Vendored TradingView CLI used by `/api/ohlcv` (override for a non-standard install) |
| `TRADING_UI_OHLCV_FIXTURE` | _unset_ | Path to a recorded `tv bars` JSON envelope; when set, `/api/ohlcv` reads it instead of running the CLI (offline dev / tests) |

IB account/positions also read these standard IB env vars (loaded from the repo
`.env` like the scheduler): `IB_PAPER_TRADING` (paper vs live label),
`IB_GATEWAY_RUNTIME_DIR` (override the `ib-gateway/.runtime` session location).

## Authentication (optional)

The server binds to loopback only, but it can spawn real scheduler processes, so
an optional username/password gate is built in. Set **both** `UI_AUTH_USER` and
`UI_AUTH_PASSWORD` in `ui/.env` (or the repo-root `.env` — both are loaded) to
require login; leave either unset and the dashboard behaves as before (no login).

```bash
# ui/.env
UI_AUTH_USER=trader
UI_AUTH_PASSWORD=change-me
```

On first visit an unauthenticated client sees a login screen (`POST /api/login`).
A successful login sets a signed, **httpOnly** session cookie
(`SameSite=Lax`, default 7-day TTL via `UI_AUTH_TTL_HOURS`); no credentials are
stored on the client. The cookie — rather than a header — is used so that SSE
log streams (`EventSource`) and chart `<img>` requests authenticate too. The
token is signed with `UI_AUTH_SECRET` (or a key derived from the credentials, so
changing the password invalidates every outstanding session). All `/api/*`
routes are gated except `/api/health` and the auth endpoints (`GET /api/auth`
status, `POST /api/login`, `POST /api/logout`); the "🚪 Выйти" button in the
top bar logs out. A session that expires mid-use bounces back to the login
screen automatically.

## API surface

Read (each accepts `?date=YYYY-MM-DD`, defaults to the latest available):
`/api/dates`, `/api/exposure`, `/api/watchlist`, `/api/portfolio`, `/api/market`,
`/api/screeners`, `/api/theses` (+ `/:id`), `/api/signals`, `/api/profile`,
`/api/autopilot`, `/api/analysis/tickers` (which tickers already have saved
analysis), `/api/ticker/:symbol[/:date[/chart/:tf]]`.

Live (no `?date`): `/api/ib` — a read-only Interactive Brokers snapshot
(account balances + open positions + working orders + recent trade history)
behind the **Счёт IB** tab. The server shells out to
`skills/ib-portfolio-manager/scripts/fetch_ib_snapshot.py`, which locates the
bundled IB Gateway session and queries the Client Portal REST API (strictly
GET-only — `/portfolio/*`, `/iserver/account/orders` and
`/iserver/account/trades`; never places an order). Trade history covers the
current day plus ~6 prior days (the Gateway's window), newest first. The tab has
an **«Обновить»** button that re-fetches on demand.
When the Gateway is down or unauthenticated the response is `{ ok: false, error }`
and the tab renders a friendly notice (with the same refresh button, so you can
retry after completing IB login / 2FA). Set `TRADING_UI_IB_FIXTURE` to serve a
recorded snapshot without a live connection.

`/api/ib/health` — a cheap Gateway **liveness probe** (`{ ok, reachable,
authenticated, port, error, source, checked_at }`), meant to be polled on an
interval. It avoids the Python snapshot entirely: it reads the Gateway's
`gateway-session.json` for the port and POSTs the Client Portal
`/iserver/auth/status` endpoint (self-signed cert, short timeout). The client
polls it (independent of the active tab) and turns the **Счёт IB** tab red with
a `●` marker + tooltip whenever `ok:false`. Honors `TRADING_UI_IB_FIXTURE`
(derives health from the fixture's `ok`).

Live (no `?date`): `/api/ohlcv/:symbol?tf=D&n=300` — read-only OHLCV bars from
the live TradingView data layer. The server shells out to the vendored `tv`
CLI (`tv bars … -t <tf>`), normalizes the envelope, and degrades to
`{ ok: false, error }` when TradingView Desktop isn't running with CDP on :9222.
`tf` ∈ `D/W/M/240/120/60/30/15/5`; `n` is clamped to 20–500. Clicking a ticker
in the **Watchlist** opens a candlestick + volume + MA(20/50/200) modal chart
with the row's entry/stop/target overlaid as price lines (lightweight-charts,
lazy-loaded). The per-row link to the saved analysis report sits next to the
**Analyze** button. Set `TRADING_UI_OHLCV_FIXTURE` to serve recorded bars
without a live connection.

Watchlist reconcile: `GET /api/watchlist/reconcile/:ticker` previews how the
analysis signal would change the candidate; `POST` applies it to the watchlist
file in place.

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
be cancelled.

**Reconcile with the watchlist.** When the analysis finishes, the modal compares
the fresh analysis signal (parsed from the priority scenario in `signals.md`)
against the screener-derived watchlist candidate and classifies the change:
`direction-flip` (e.g. short → long), `levels-updated`, `new`, or `unchanged`.
**Apply to watchlist** writes the merged candidate in place. Decision rule: the
analysis signal is authoritative — `side` ← direction, `pivot` ← Trigger,
`stop` ← Stop, `target` ← T1 — shares are re-derived as
`risk_dollars / |pivot − stop|` to keep the same dollar-risk, the original
screener values are preserved under `screener_origin`, and the candidate is
tagged `source: "analysis"`. The watchlist table shows a `screener`/`analysis`
source pill per candidate. On completion the analysis
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
