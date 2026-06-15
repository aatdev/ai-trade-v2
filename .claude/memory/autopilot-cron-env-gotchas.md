---
name: autopilot-cron-env-gotchas
description: trading autopilot runs under cron with a minimal env — PATH and Claude login both break unless fixed
metadata: 
  node_type: memory
  type: project
  originSessionId: f0936442-e171-4aeb-9497-38ea0df5f730
---

`run_trading_autopilot.py` runs from crontab, so it inherits cron's minimal environment: `PATH=/usr/bin:/bin` and no access to the GUI/Aqua security session. Two failure modes seen on 2026-06-10 (evening-prep rc=1):

**Cadence (changed 2026-06-15):** cron now fires `*/5` (was `*/15`) for tighter intraday price checks, and the crontab line also exports `INTRADAY_INTERVAL_MIN=5`. Both halves must match — the cron step AND the dedup interval — or the intraday slot self-dedupes and the extra ticks are no-ops. `INTRADAY_INTERVAL_MIN` is read at **module import** (default 15), which is BEFORE `load_env_file()` runs, so it must be set in the crontab env (`VAR=val ... python3`) or process env — putting it in `.env` does NOT work. To retune cadence, edit the live crontab (`crontab -e`); the constant lives in `scripts/run_trading_autopilot.py`.

1. **`FileNotFoundError: 'node'`** — the vendored `tv` CLI shells out to `node` (`/opt/homebrew/bin/node`); neither `node` nor `tv` is on cron's PATH. Fixed in code: `run_trading_schedule.ensure_runtime_path()` (called at module level) prepends `/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin`. A bare `PATH=` line in `.env` does NOT help because `load_env_file()` uses `setdefault` and cron already exports PATH.

2. **`Not logged in · Please run /login`** — Claude's OAuth login lives in the macOS login Keychain, which cron can't unlock. Fix is operational, not code: run `claude setup-token` once, then add `CLAUDE_CODE_OAUTH_TOKEN=<token>` to the gitignored `.env`. `load_env_file()` injects it under cron and `claude -p` uses it instead of the keychain.

3. **`ModuleNotFoundError: No module named 'yaml'`** (seen 2026-06-13, ibd-distribution-days slot rc=1) — the scheduler spawns every skill script via `sys.executable`, but `run_trading_schedule.sh` launched it with bare `python3` → Homebrew `/opt/homebrew/bin/python3`, which lacks the declared deps (`pyyaml`, `pandas`, `numpy`). The project `.venv` has the full set (pyproject `dependencies`). `ibd_monitor.py` is the only scheduled skill that `import yaml` at top level, so it was the first to break. Fixed in code two ways: `.sh` now prefers `.venv/bin/python` when present, and `run_trading_schedule.ensure_venv_interpreter()` (called at the top of `main()`) re-execs into `.venv/bin/python` to self-heal cron/manual `python3 …py` invocations that bypass the launcher.

**Why:** scheduled/headless runs never source the interactive shell env or the GUI keychain. **How to apply:** when an autopilot slot fails rc=1, check the slot log under `trading-data/logs/autopilot/` for these two signatures first. Related: [[vendored-tv-data-layer]], [[tv-cli-shadowing-jackson]], [[trading-data-layout]].
