---
name: claude-pee-nested-session-breaks
description: "claude-pee silently no-ops (rc=0, empty stdout) when run inside an active Claude Code session — breaks the claude step of slash-command slot runs"
metadata: 
  node_type: memory
  type: project
  originSessionId: 38a5cfa6-61df-41ee-be92-ee8f46eae93f
---

The scheduler's claude steps run via `claude-pee` (compiled Rust PTY wrapper at /usr/local/bin/claude-pee): it spawns a child interactive `claude`, injects the prompt, polls for the session transcript `<session-id>.jsonl`, and harvests the answer, terminating via a Stop-hook sentinel file.

**Gotcha:** when launched from *inside* an active Claude Code session, the child claude inherits `CLAUDECODE=1`, `CLAUDE_CODE_CHILD_SESSION=1`, `CLAUDE_CODE_SESSION_ID=…` and never produces the expected transcript. claude-pee gives up after ~4s (`RUST_LOG=debug` → `claude_pee::transcript phase=1 stopped (no transcript found)`) and exits **rc=0 with empty stdout**. Confirmed: `claude-pee -p "Reply OK"` returns nothing nested, but returns `OK` once `env -u CLAUDECODE -u CLAUDE_CODE_CHILD_SESSION -u CLAUDE_CODE_SESSION_ID -u CLAUDE_CODE_ENTRYPOINT -u CLAUDE_CODE_EXECPATH` strips the markers.

**Impact:** every slash-command slot run (`/weekly`, `/evening-prep`, `/intraday`, `/premarket`, `/monthly`) is nested → deterministic skill scripts run fine but the claude synthesis/market-top/ticker step silently no-ops, and the slot still reports success + sends Telegram. cron/launchd and plain-terminal runs are NOT nested, so they work there.

**Two defects in `scripts/run_trading_schedule.py` — FIXED 2026-06-13:** (1) `run_claude` now strips the nested-session env via `_child_claude_env()` before invoking claude-pee; (2) `run_claude` gained `expected_output` and no longer treats `rc==0` alone as success (requires the expected file non-empty, else non-empty stdout) — so a fast empty exit now yields `rc=1`, not a silent DONE. Wired at weekly/monthly/regime-gate/chart-validation callers; ticker-analysis uses the stdout fallback. Tests in `scripts/tests/test_trading_schedule.py`.

**Deeper, still-OPEN issue (separate from the two defects):** even with env stripped, a *nested* `claude-pee` reliably ENGAGES (real stdout, no longer empty) but its inject-prompt-into-interactive-TUI model captures a session-start GREETING ("Привет! Готов к работе… Чем займёмся?") instead of executing the `-p` task — likely the 500ms quiescence is too short while the child loads CLAUDE.md/memory/MCP. So `/weekly` etc. now FAIL LOUDLY (rc=1, "no expected output") rather than producing the JSON. Reliable path = run the claude step IN-SESSION (e.g. for weekly: WebSearch 50DMA breadth + put/call → `market_top_detector.py --breadth-50dma --put-call --output-dir trading-data/market/` → write `weekly_review_<date>.json` per the build_weekly_msg schema). Possible (untested) automated fix: raise `CLAUDE_PEE_QUIESCE_MS` in `run_trading_schedule.sh`.

**Why:** so I don't re-diagnose the "10s false-success, JSON not created" symptom from scratch. **How to apply:** when a slot's claude step finishes suspiciously fast with no output JSON, suspect nested-session env, not rate limits; if it engages but greets, that's the injection-timing issue → do the claude step in-session. Related: [[autopilot-cron-env-gotchas]] (the opposite — cron's bare env), [[trading-data-layout]].
