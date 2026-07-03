---
name: scheduler-claude-steps-ambient-ib-mcp-hang
description: "scheduler claude-p steps loaded ambient .mcp.json (interactive-brokers); IB Gateway cold-boot on the FIRST slot step hangs → StopTimeout → fail-safe RESTRICT. Fixed with --strict-mcp-config default."
metadata:
  type: project
---

The scheduler's `run_claude` steps (market-regime-daily gate, chart-validation,
weekly/monthly synthesis) launched `claude-p` WITHOUT any `--mcp-config`, so each
inherited the ambient project `.mcp.json` — whose only server is
`interactive-brokers` (bundled IB Gateway, headless SSO/2FA). None of those steps
call an IB tool (they run local scripts + WebSearch), but Claude Code still spins
the server up at session init. The IB Gateway **cold-boot** blocks whichever
claude-p step runs FIRST in a slot; if it hangs, the inner claude never writes a
transcript and `claude-p` burns the whole `--timeout` budget then exits
`StopTimeout` (rc=2) → `run_regime_gate` fail-safes the exposure gate to RESTRICT
→ evening-prep returns rc=1.

**Diagnosis heuristic:** a `claude-p: StopTimeout` (rc=2) where the run consumed
the FULL `--timeout` (e.g. 1771s of 1770) AND left **no session transcript** under
`$CLAUDE_CONFIG_DIR/projects/<repo>/*.jsonl` = the hang is pre-transcript = session
init, not a tool call. Confirmed 2026-07-01: evening-prep 22:15 regime step hung
1771s with no transcript; the 22:57 chart-validation step (same process, same
`.mcp.json`) ran fine in 248s because the gateway was warm by then; the 23:10
autopilot retry succeeded in 205s (warm gateway) and wrote a real gate.

**Fix (2026-07-01):** `run_claude` now appends `--strict-mcp-config` (with NO
`--mcp-config` ⇒ zero MCP servers) by default, via `_mcp_disable_flags(extra)`.
Suppressed only when the caller already declared MCP intent
(`--mcp-config`/`--strict-mcp-config` in `extra`) — i.e. the ticker-analysis path
(`_run_ticker_analysis`) which opts into the TradingView MCP. Verified live that
`claude-p ... --strict-mcp-config` (lone, no config) is accepted and starts a
zero-MCP session (returns normally). Tests: `TestMcpDisableFlags` + 3 e2e cases.

**Why:** so a slow/hung slot isn't re-diagnosed as a rate-limit or a genuinely
slow workflow. **How to apply:** the workflow itself is ~3 min; if a regime/chart
step eats minutes, suspect ambient MCP init, not the skills. Related:
[[claude-p-wrapper-semantics]] (wrapper `--timeout` default 300s, prompt
positional), [[ib-headless-autologin]] (IB login = authenticate tool, may need
mobile approval), [[ib-gateway-probe-gotchas]], [[autopilot-cron-env-gotchas]].
