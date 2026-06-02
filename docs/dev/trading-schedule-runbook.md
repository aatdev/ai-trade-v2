# Trading Schedule Runbook (CET swing-trading day plan)

Scheduled orchestrator that runs the European swing-trading day plan against US
equities (NYSE/NASDAQ open 15:30–22:00 CET). Times are **machine local wall
clock**; for a CET-based trader that equals CET/CEST and `launchd` auto-adjusts
across DST, keeping the slots anchored to the US open/close.

## Components

| File | Role |
|------|------|
| `scripts/run_trading_schedule.py` | Orchestrator (stdlib-only). Slots, calendar gates, headless `claude -p` runner, exposure gate, Telegram. |
| `scripts/run_trading_schedule.sh` | Thin launcher for `launchd`; sources `.env` (FMP / ALPACA / TELEGRAM) and `.envrc`. |
| `launchd/com.trade-analysis.trading-premarket.plist` | ~15:00 CET, Mon–Fri |
| `launchd/com.trade-analysis.trading-evening-prep.plist` | ~22:15 CET, Mon–Fri |
| `launchd/com.trade-analysis.trading-monthly.plist` | Sundays 11:00 (script keeps only the 1st Sunday) |
| `scripts/tests/test_trading_schedule.py` | Unit tests (calendar gates, fail-safe gate parsing, slot dispatch). |

## Hybrid execution model

The plan mixes deterministic steps with discretionary, broker-manual ones, so
the orchestrator is **hybrid**:

- **Auto (headless `claude -p`)** — `market-regime-daily`,
  `swing-opportunity-daily`, `monthly-performance-review`.
- **Reminder only (Telegram, human in the loop)** — `swing-execution-manage`
  (entry on breakout trigger, in-trade trim/trail, exit on stop/target/break)
  and `trade-memory-loop` (postmortem after a close). These involve live
  triggers and manual broker orders and are never auto-executed.

## The exposure gate

`market-regime-daily` is prompted to write a machine-readable gate file:

```
reports/schedule/exposure_decision_<YYYY-MM-DD>.json
{ "decision": "allow" | "restrict" | "cash-priority", ... }
```

New swing risk (`swing-opportunity-daily` + entries) proceeds **only** when
`decision == allow`. On `restrict` / `cash-priority`, or if the gate file is
missing/unparseable (**fail-safe → `restrict`**), the orchestrator only reminds
to manage / close open positions.

## Slots

| Slot | When | What runs |
|------|------|-----------|
| `premarket` | ~15:00 CET, trading days | Quick regime re-check → Telegram: if `allow`, arm bracket orders / breakout triggers for the evening watchlist; else manage/close only. Always reminds about intraday management + `trade-memory-loop` on closes. |
| `evening-prep` | ~22:15 CET, trading days | Full regime on fresh EOD data → if `allow`, run `swing-opportunity-daily` to build `reports/schedule/watchlist_<date>.json` → Telegram summary (+ file). |
| `monthly` | 1st Sunday ~11:00 CET | `monthly-performance-review` → Telegram summary of decision log + next-month rule changes. |

Non-trading days (weekends + US market holidays) and non-first-Sundays are
skipped automatically.

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

## Manual / test runs

```bash
# Dry-run: print the prompts + intended Telegram messages, call nothing.
python3 scripts/run_trading_schedule.py --slot evening-prep --dry-run --no-telegram

# Force-run ignoring the trading-day / first-Sunday gate.
python3 scripts/run_trading_schedule.py --slot monthly --date 2026-06-07 --force --dry-run

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
uv run pytest scripts/tests/test_trading_schedule.py -q
```

> **Maintenance:** extend `US_MARKET_HOLIDAYS` in `run_trading_schedule.py` each
> year. An out-of-date list at worst runs a screen on a holiday (harmless — stale
> EOD data) or skips one; it never places a trade.
