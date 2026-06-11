---
name: autopilot-cron-env-gotchas
description: trading autopilot runs under cron with a minimal env — PATH and Claude login both break unless fixed
metadata: 
  node_type: memory
  type: project
  originSessionId: f0936442-e171-4aeb-9497-38ea0df5f730
---

`run_trading_autopilot.py` runs from crontab (`*/15 * * * *`), so it inherits cron's minimal environment: `PATH=/usr/bin:/bin` and no access to the GUI/Aqua security session. Two failure modes seen on 2026-06-10 (evening-prep rc=1):

1. **`FileNotFoundError: 'node'`** — the vendored `tv` CLI shells out to `node` (`/opt/homebrew/bin/node`); neither `node` nor `tv` is on cron's PATH. Fixed in code: `run_trading_schedule.ensure_runtime_path()` (called at module level) prepends `/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin`. A bare `PATH=` line in `.env` does NOT help because `load_env_file()` uses `setdefault` and cron already exports PATH.

2. **`Not logged in · Please run /login`** — Claude's OAuth login lives in the macOS login Keychain, which cron can't unlock. Fix is operational, not code: run `claude setup-token` once, then add `CLAUDE_CODE_OAUTH_TOKEN=<token>` to the gitignored `.env`. `load_env_file()` injects it under cron and `claude -p` uses it instead of the keychain.

**Why:** scheduled/headless runs never source the interactive shell env or the GUI keychain. **How to apply:** when an autopilot slot fails rc=1, check the slot log under `trading-data/logs/autopilot/` for these two signatures first. Related: [[vendored-tv-data-layer]], [[tv-cli-shadowing-jackson]], [[trading-data-layout]].
