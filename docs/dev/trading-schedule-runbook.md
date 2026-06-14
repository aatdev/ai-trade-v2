# Trading Schedule Runbook (CET swing-trading day plan)

Scheduled orchestrator that runs the European swing-trading day plan against US
equities (NYSE/NASDAQ open 15:30–22:00 CET). Times are **machine local wall
clock**; for a CET-based trader that equals CET/CEST and `launchd` auto-adjusts
across DST, keeping the slots anchored to the US open/close.

## Components

| File | Role |
|------|------|
| `scripts/run_trading_schedule.py` | Orchestrator (stdlib-only). Slots, calendar gates, headless `claude -p` runner, deterministic skill-script runner (always `TV_NO_CACHE=1` — live chart data only), exposure gate, Telegram. |
| `scripts/lib/trading_signals.py` | Auto-mode signal engine (stdlib-only): headless quotes via the public TradingView scanner, watchlist builder, OPEN/CLOSE signal evaluation, once-per-day dedup state. |
| `scripts/lib/tv_alerts.py` | TV alert bridge: CDP availability probe, watchlist → alert plan (`[WL]`-tagged Trigger/Stop/T1), sync/purge via the signals-alerts Node scripts. Manual signals.md alerts are never touched (`--message-contains [WL]`). |
| `scripts/run_trading_autopilot.py` | Self-dispatching cron wrapper (`*/15 * * * *`): picks the due slot, retries, state, important-only Telegram. |
| `scripts/run_trading_schedule.sh` | Thin launcher for `launchd`; sources `.env` (FMP / ALPACA / TELEGRAM) and `.envrc`. |
| `launchd/com.trade-analysis.trading-premarket.plist` | ~15:00 CET, Mon–Fri |
| `launchd/com.trade-analysis.trading-evening-prep.plist` | ~22:15 CET, Mon–Fri |
| `launchd/com.trade-analysis.trading-monthly.plist` | Sundays 11:00 (script keeps only the 1st Sunday) |
| `scripts/tests/test_trading_{schedule,autopilot,signals}.py` | Unit tests (calendar gates, fail-safe gate parsing, slot dispatch, signal engine, autopilot windows). |

## Hybrid execution model (auto mode)

The trader only opens/closes positions on script signals; everything else is
automated as far as possible:

- **Deterministic (no claude)** — vcp-screener → portfolio heat →
  breakout-trade-planner (evening long branch), swing-short-screener (evening
  short branch), the intraday quote monitor (public `scanner.tradingview.com`,
  no API key, no TradingView Desktop), IBD/macro/FTD weekly reports.
- **Headless `claude -p`** — `market-regime-daily` (gate), chart validation of
  the top candidates (technical-analyst verdicts → `watchlist_validation_*.json`),
  weekly market-top (WebSearch for 50DMA breadth + put/call), and
  `monthly-performance-review`.
- **Human in the loop (Telegram signals, never auto-executed)** — placing
  bracket orders on 🟢 ОТКРОЙ signals, trimming/closing on ⛔️/⚠️/💰 signals,
  `trade-memory-loop` after a close.

## The exposure gate

`market-regime-daily` is prompted to write a machine-readable gate file:

```
reports/schedule/exposure_decision_<YYYY-MM-DD>.json
{ "decision": "allow" | "restrict" | "cash-priority", ... }
```

New swing risk (`swing-opportunity-daily` + entries) proceeds **only** when
`decision == allow`. On `restrict` / `cash-priority`, or if the gate file is
missing/unparsable (**fail-safe → `restrict`**), the orchestrator only reminds
to manage / close open positions.

## Slots

| Slot | When | What runs |
|------|------|-----------|
| `premarket` | ~15:00 CET, trading days | Heat refresh + quick regime re-check → Telegram: if `allow`, arm bracket orders / breakout triggers for the evening watchlist; else manage/close only. |
| `intraday` | 15:30–22:00 CET, trading days, every ~15 min | No claude. Quotes for watchlist + open positions via the public TV scanner → `evaluate_signals` → Telegram: 🟢 ОТКРОЙ ЛОНГ / 🔻 ОТКРОЙ ШОРТ (trigger crossed, capacity-checked), 🚫 не гнаться, ⛔️ стоп задет, ⚠️ у стопа, 💰 +2R. Each signal once a day (`logs/intraday_signals_state.json`). MISSED candidates get their `[WL]` TV alerts purged immediately (or a "remove manually" warning when TV is down). |
| `evening-prep` | ~22:15 CET, trading days | Full regime via claude → gate. `allow`: vcp-screener → heat → breakout-trade-planner → claude chart validation (technical-analyst pass/reject **+ authoritative entry/stop/target levels** that override the planner's mechanical ones; size recomputed from the risk profile) → `schedule/watchlist_<date>.json` + thesis ingest → full `ticker-analysis` deep dive on the **single** best candidate not analyzed within `FRESH_ANALYSIS_WEEKDAYS` (reconcile levels / direction-flip; `AUTO_ANALYZE_TOP_N=1`). `restrict`/`cash-priority`: short branch (market pressure: top-risk ≥ 41 or DD ≥ 3, no fresh FTD) → swing-short-screener → short watchlist (1% risk sizing). Then TV alert sync: create `[WL]` Trigger/Stop/T1 alerts for candidates, drop stale ones (state: `logs/watchlist_alerts_state.json`). Requires TradingView Desktop (live data, no cache) — when it is down the slot notifies Telegram IMMEDIATELY and returns rc 1 (autopilot retries). Telegram summary (+ file, + alert counts). |
| `weekly` | Saturday ~12:00 CET | IBD distribution days (QQQ/SPY), macro regime, FTD detector (deterministic) + market-top via claude/WebSearch → `schedule/weekly_review_<date>.json` → Telegram. |
| `monthly` | 1st Sunday ~11:00 CET | `monthly-performance-review` → Telegram summary of decision log + next-month rule changes. |

Non-trading days (weekends + US market holidays), non-Saturdays (weekly) and
non-first-Sundays (monthly) are skipped automatically.

## Install

```bash
# Make sure .env holds FMP_API_KEY / ALPACA_* / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
for s in premarket evening-prep monthly; do
  sed "s|\$HOME|$HOME|g; s|\$PROJECT_DIR|$(pwd)|g" \
    "launchd/com.trade-analysis.trading-$s.plist" \
    > "$HOME/Library/LaunchAgents/com.trade-analysis.trading-$s.plist"
  launchctl load "$HOME/Library/LaunchAgents/com.trade-analysis.trading-$s.plist"
done
launchctl list | grep trading-
```

### Autopilot (cron, recommended for auto mode)

```bash
crontab -e
# every 15 minutes — the intraday monitor needs this cadence; other slots dedupe
*/15 * * * * cd <repo> && /usr/bin/python3 scripts/run_trading_autopilot.py >> trading-data/logs/autopilot_cron.log 2>&1
```

## Manual / test runs

```bash
# Dry-run: print the prompts + intended Telegram messages, call nothing.
python3 scripts/run_trading_schedule.py --slot evening-prep --dry-run --no-telegram

# Intraday monitor outside the session window (window/calendar gates bypassed).
python3 scripts/run_trading_schedule.py --slot intraday --force --no-telegram

# Force-run ignoring the trading-day / Saturday / first-Sunday gate.
python3 scripts/run_trading_schedule.py --slot weekly --force --dry-run
python3 scripts/run_trading_schedule.py --slot monthly --date 2026-06-07 --force --dry-run

# Autopilot decision check for any wall-clock moment (state not mutated).
python3 scripts/run_trading_autopilot.py --now 2026-06-11T16:05:00 --dry-run

# Real one-off (uses .env via the wrapper).
bash scripts/run_trading_schedule.sh --slot premarket
```

### Tuning the headless run (env vars)

| Var | Default | Purpose |
|-----|---------|---------|
| `CLAUDE_BIN` | `claude` | Path to the Claude CLI. |
| `CLAUDE_CONFIG_DIR` | (unset) | Pass through if you keep a non-default config dir. |
| `TRADING_SCHEDULE_PERMISSION_MODE` | `bypassPermissions` | `--permission-mode` for unattended runs. |
| `TRADING_SCHEDULE_CLAUDE_FLAGS` | (empty) | Extra `claude -p` flags appended verbatim. |
| `TRADING_SCHEDULE_TIMEOUT` | `1800` | Per-workflow timeout (s). |

## Logs

- `logs/trading_schedule.log` — orchestrator log via the `logging` module:
  levelled records (INFO / WARNING / ERROR), per-run `RUN START`/`RUN END`
  banners with PID and elapsed time, and per-workflow `START`/`DONE` durations.
  Daily rotation at midnight, 30-day retention. Use `-v`/`--verbose` for DEBUG.
- `logs/launchd_trading_*.log` / `*_error.log` — launchd stdout/stderr.

## Tests

```bash
uv run pytest scripts/tests/test_trading_schedule.py \
              scripts/tests/test_trading_autopilot.py \
              scripts/tests/test_trading_signals.py -q
```

> **Maintenance:** extend `US_MARKET_HOLIDAYS` in `run_trading_schedule.py` each
> year. An out-of-date list at worst runs a screen on a holiday (harmless — stale
> EOD data) or skips one; it never places a trade.
