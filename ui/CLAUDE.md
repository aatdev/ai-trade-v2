# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Scope: the `ui/` subproject — a local **State Dashboard** for the trading scheduler.
For the repo-wide skill conventions see the root `CLAUDE.md`. For the full feature
and HTTP API catalog see [`ui/README.md`](./README.md); for the visual design system
(color tokens, typography, component styling) see [`ui/DESIGN.md`](./DESIGN.md). This
file covers the **internal architecture and editing conventions** that they do not.

## Commands

Run all from `ui/`. This is an **npm-workspaces monorepo** (`server`, `client`); the
root `package.json` orchestrates both.

```bash
npm install                       # installs both workspaces
npm run dev                       # concurrently: server :4000 + client :5173 (Vite proxies /api → :4000)
npm run build                     # client (tsc --noEmit + vite build) THEN server (tsc) — order matters (prod serves client dist)
npm start                         # prod single-port: node server/dist/index.js on 127.0.0.1:4000
npm test                          # vitest — SERVER ONLY (there are no client tests)
npm run typecheck                 # server (tsc) + client (tsc --noEmit)

# single test file / pattern (run from ui/server, or use -w server from ui/)
npm test -w server -- src/lib/reconcile.test.ts
npm test -w server -- -t "rejects unknown slot"
npm run test:watch -w server
```

There is no linter in this subproject; `npm run typecheck` is the static gate.

## Big-picture architecture

Three parts, wired by a **type-only contract**:

- **`server/`** — Express + TypeScript (CommonJS, built with `tsc`, dev via `tsx watch`).
  Reads the scheduler's output under `trading-data/` and spawns whitelisted commands.
- **`client/`** — React 18 + TypeScript + Vite SPA. TanStack Query for all server
  state, react-router (`/` Dashboard, `/ticker/:symbol[/:date]` TickerDetail).
- **`shared/types.d.ts`** — `import type`-only API contract, imported by both sides
  via the `@shared/*` path alias (configured in both tsconfigs + `vite.config.ts` +
  `vitest.config.ts`). It is **never compiled or bundled** — the imports erase at
  runtime. Any change to a request/response shape goes here first, then both sides.

### The server mirrors the Python scheduler (this is load-bearing)

`server/src/config.ts` deliberately re-implements `scripts/run_trading_schedule.py`
helpers so spawned subprocesses behave identically to a cron-launched scheduler:

- `findProjectRoot()` — honors `TRADING_PROJECT_ROOT`, else walks up looking for
  `scripts/run_trading_schedule.py` (the `PROJECT_MARKER`).
- `resolveTradingDataDir()` — mirrors `_resolve_trading_data_dir()`: env var is
  **`TRADING_DATE_DIR`** (note: DATE, not DATA), else `trading-data/` under the root.
- `ensureRuntimePath()` mirrors `ensure_runtime_path()` (prepends Homebrew/`~/.local/bin`
  so `claude`/`tv`/`node` resolve under a minimal cron PATH).
- `loadDotEnv()` mirrors `load_env_file()` (repo-root `.env` then `ui/.env`, never
  overriding already-set vars) so spawned `claude`/skill processes see secrets.

If you change path/PATH/env resolution in the Python scheduler, mirror it here.

### Read path: files → mappers → Sourced<T>

State endpoints are **read-mostly** — they never mutate `trading-data/`. The pipeline:

1. **`lib/files.ts`** — fail-safe directory/file helpers with a 5 s listing cache.
   Scheduler filenames embed `YYYY-MM-DD[_HHMMSS]`, so a plain lexicographic sort is
   chronological: `findLatest` / `listLatest` / `resolveFile` rely on this. `?date=`
   filters to a date token; `?source=` (or `*Source=`) pins a specific historical
   file. **Path-traversal safety = strict filename-regex test + directory-membership
   check** in `resolveFile` — keep both when adding a sourced endpoint.
2. **`lib/mappers.ts`** — defensive coercion (`numOrNull`/`strOrNull`/`asArray`/`asRecord`)
   turns raw scheduler JSON/YAML into typed shapes; never throws on a missing/malformed
   file (returns `null` data). Filename patterns live in the exported `RE` map.
3. Every state response is wrapped in **`Sourced<T> = { date, source, data }`** so the
   client knows which file it actually got.

To add a state endpoint: add a pattern to `RE`, a getter+mapper in `mappers.ts`, wire
it into `routes/state.ts` (add to `VERSION_KINDS` if it should be pin-selectable), add
the type to `shared/types.d.ts`, add a hook in `client/src/api.ts`, and add a fixture
under `server/test/fixture/` + a test.

### Write path: JobManager + whitelisted actions

All process-spawning and real mutations go through `routes/actions.ts` +
`routes/screener.ts`, backed by **`lib/jobs.ts` (`JobManager`)**:

- **Resource-lane locking** (server-side): each job declares a `lane?: JobLane` it
  contends for. Jobs sharing a lane **serialize** — a second start while one holds the
  lane returns `{ busy: true, activeJobId, lane }` (HTTP 409); jobs on **different** lanes
  run **concurrently** (so a ticker analysis, a VCP screener, a slot, and a memory write
  all run at once). The lanes:
  - `scheduler` — `run-slot`, `recalc-profile` (the Python scheduler's single-run lock;
    exit code **75** = busy, `SCHEDULER_BUSY_CODE` → job status `busy`).
  - `tradingview` — `analyze-ticker`, `sync-alerts`, `sync-thesis-alerts`, `delete-alerts`
    (one TradingView Desktop instance over CDP :9222).
  - `ib` — `place`/`cancel`-bracket, `cancel-ib-order`, `sync-ib-fills` (one IB Gateway session).
  - `screener` — VCP / short / bottom-flow run, plan, save-watchlist (shared staging files).
  - **no lane** — `/actions/memory`, `/actions/delete-theses` (the trader-memory CLI guards
    its own cross-process `_index.lock`): lane-less jobs never lock and are never blocked,
    so a thesis status change can't be spuriously refused `busy` behind a long lane job.
  `JobManager.activeLanes` (`Record<lane, jobId>`) is surfaced by `GET /actions/jobs`.
- Log output is a per-job ring buffer (2000 lines), streamed to the client over **SSE**
  (`GET /actions/jobs/:id/stream`): replay buffered lines, then live; `end` event on close.
  The **Задания** client tab (`components/JobsTab.tsx`) polls `GET /actions/jobs` and is the
  central monitor — list all jobs, view any job's log (`lib/useJobLog.ts`), jump to a finished
  job's result, or cancel a running one. Per-panel run buttons stay where they are.
- Commands are a **strict whitelist**. Shelling out goes through canonical launchers
  (`scripts/run_trading_schedule.sh` for slots, `scripts/run_watchlist_orders.sh` for IB
  brackets) that load `.env` / fix PATH / pick the repo `.venv` — never invoke the
  underlying Python directly when a launcher exists (it carries the env + safety locks).

**Security invariants when touching actions** (`routes/actions.ts`, `routes/state.ts`):
- Never trust client-claimed state. Thesis status is **re-read** from the index/detail
  before a bracket is placed (`place-ib-bracket` refuses unless ENTRY_READY); delete only
  applies to IDEA/ENTRY_READY/INVALIDATED per the authoritative index.
- Validate every interpolated arg against a regex (`TICKER_RE`, `DATE_RE`, `THESIS_ID_RE`,
  `ORDER_ID_RE`, `SOURCE_RE`). `shell: true` jobs are only for fixed, input-free pipelines.
- `run-slot` defaults to `--dry-run` unless the body explicitly sets `dryRun: false`.
- IB order placement is **triple-gated**: the UI click + the always-passed `--live` flag +
  `IB_ALLOW_ORDER_PLACEMENT=true` in `.env`. Without the env flag every run is a no-post
  preview. `/api/ib` (snapshot) and `/api/ib/health` (liveness probe) are strictly read-only.

### Auth

Optional, off unless **both** `UI_AUTH_USER` + `UI_AUTH_PASSWORD` are set (`lib`/`auth.ts`).
`createApp` mounts `/api/health` + auth endpoints publicly, then `requireAuth` gates the
rest. The session is a **signed httpOnly cookie** (not a header) specifically so `<img>`
chart requests and `EventSource` SSE streams authenticate too. The client treats any
**401** as an `auth:unauthorized` window event (`api.ts`) → `AuthGate` bounces to login.

### Client conventions

- `client/src/api.ts` is the single source of server access — one TanStack Query hook
  (or mutation fn) per endpoint. Add new endpoints here, not ad-hoc `fetch` in components.
- SSE job/analysis streams are consumed via `lib/useJobStream.ts` / `lib/useAnalyzeRun.ts`;
  Claude `stream-json` events are summarized in `lib/claudeEvents.ts`.
- `lightweight-charts` (candlestick modal) is lazy-loaded.
- Visual styling follows the design system in [`ui/DESIGN.md`](./DESIGN.md) (Apple gallery:
  white / parchment `#f5f5f7` canvas, SF Pro via `system-ui` with an Inter fallback, a single
  Action Blue `#0066cc` accent, pill CTAs + 8px utility radii, 18px hairline cards, no chrome
  shadows — depth comes from surface color, and `scale(0.95)` is the press micro-interaction)
  — consult it before adding or restyling UI. It is implemented entirely through CSS variables
  + shared classes in `client/src/styles.css` (the whole app is token-driven, so restyling
  there cascades app-wide); fonts load in `index.html`, the light/dark token blocks live at
  the top of `styles.css`. Interactive controls are real buttons — no action is styled as a
  bare text link (`.ticker-btn`, `.link-btn`, `.analysis-link`, `.ci-desc-toggle`, `.breadth-link`
  are compact ghost-pill buttons); genuine `<a>`/`<Link>` navigation stays a text link.
- The server binds **`127.0.0.1` only** (it spawns real processes) — don't change the bind
  address.

### Bundled docs

`server/content/*.ru.md` are UI-bundled Russian skill docs served by `routes/docs.ts`
through a **server-controlled manifest** (`DOC_SECTIONS`) — `file` is never user input, so
an unknown `:id` just 404s (no traversal). `CONTENT_DIR` resolves to `server/content` in
both dev (`src/routes`) and prod (`dist/routes`). `skill-doc/:skill` prefers a bundled
`.ru.md`, else falls back to `skills/<name>/SKILL.md` + references in the repo.

## Testing notes

- Vitest, **server-only**, Node environment, files colocated as `src/**/*.test.ts`,
  excluded from the build tsconfig. Fixtures live under `server/test/fixture/` mirroring
  the `trading-data/` layout (`schedule/`, `market/`, `screeners/`, `journal/`, `analysis/`).
- Routes are tested with `supertest` against `createApp({ dataDir, projectRoot, jobs })`
  — pass a stub `JobManager` to assert spawn args without launching real processes.
- The listing cache in `files.ts` has a TTL; `clearListCache()` exists for tests that
  mutate fixtures mid-run.
