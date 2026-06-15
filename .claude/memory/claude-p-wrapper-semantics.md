---
name: claude-p-wrapper-semantics
description: "claude-p is the headless claude -p emulator used in this repo; prompt is positional, default --timeout 300"
metadata: 
  node_type: memory
  type: reference
  originSessionId: d465026d-9305-4212-9bde-d3520d2cf512
---

`claude-p` (and `claude-pee`, v0.1.0, in /usr/local/bin) is a drop-in `claude -p`
emulator: it drives interactive `claude` inside a PTY and captures the final
message via a Stop hook. Use it instead of plain `claude` for headless runs
because a nested plain `claude -p` launched from within a Claude Code session
silently no-ops (see [[claude-pee-nested-session-breaks]]).

Key semantics (`claude-p --help`):
- Takes the prompt as a **trailing positional** `[PROMPT]` — it **rejects** `-p`/`--print`.
- Supports `--output-format text|json|stream-json`, `--model`, `--max-turns`,
  `--allowedTools`, `--permission-mode`, `--mcp-config` (variadic), `--verbose`,
  `--input-file`, `--timeout`. Unknown flags (e.g. `--strict-mcp-config`,
  `--max-budget-usd`) are forwarded verbatim to `claude`.
- **Gotcha: default `--timeout` is 300s** (wrapper wall-time cap). For long runs
  (ticker-analysis with MCP) pass an explicit larger `--timeout` or it gets killed at 5 min.
- Proven arg order (scheduler `_run_ticker_analysis` + UI): flags first,
  `--mcp-config <path> --strict-mcp-config` (the boolean flag terminates the
  variadic so the prompt isn't swallowed), then the prompt LAST.

Repo convention: `CLAUDE_BIN` env overrides the binary, else default to `claude-p`
(matches `resolve_claude_bin` in scripts/run_trading_schedule.py). The UI
analyze-ticker route (ui/server/src/routes/actions.ts → lib/analyzeTicker.ts) and
the scheduler use claude-p. The skill-generation-pipeline and skill-improvement-loop
scripts still call plain `claude -p` with the prompt via **stdin** (not migrated).
