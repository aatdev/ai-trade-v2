---
name: trading-logic-audit-remaining-backlog
description: "Verified-but-unfixed findings from the 2026-07-07 and 2026-09-24 audits (selection pipeline, sizing, gating, orders, skill health)"
metadata:
  node_type: memory
  type: project
  originSessionId: 44b17f7b-9efd-48ac-ad1c-34be4b728be2
  modified: 2026-09-24T19:24:56.171Z
---

2026-07-07 audit fixed 11 findings (commit 5dd25f0). 2026-09-24 re-audit (read-only) confirmed nearly all remaining items STILL PRESENT and found new ones. Fix plan phases 1-7 agreed 2026-09-24; phases 1-6 done; phase 7 blocked on user (real exit prices, cron enable) (gating, reconcile, VCP, swing-short, skills, data/cron) open.

**Operational state (2026-09-24):** autopilot crontab line commented out → no production runs since 2026-07-08. journal/theses holds only 4 files (_index.json rewritten 07-08 09:14, no audit trail; 07-05 monthly counted 78). Tests write to prod `trading-data/logs/trading_schedule.log` (LOG_FILE not monkeypatched); a 07-07 pytest run invalidated ~27 real short theses.

**Sizing / heat — FIXED 2026-09-24 (commit 2fe43fa, phase 1):** worst-fill sizing (tsig.risk_sized_shares), validated-short 1% leak, geometry rejection (rejected_by_geometry), pending GTC brackets reserved in heat (pending_entries), intraday/tap heat fail-closed (≤30h, HEAT_MAX_AGE_HOURS), tap-time gate re-check (gate_ok_for), short-branch heat fail-safe. Note: stale prod heat now blocks UI open-now until a slot/`trader_memory_cli heat` rebuilds it.

**Gating — FIXED 2026-09-24 (commit bf01960, phase 2):** evening deterministic regime inputs (breadth/uptrend/macro/FTD/IBD) before claude; exposure-coach re-run bounds LLM gate (clamp, ceiling ≤ coach, degraded → coach fallback); run_claude output-freshness (mtime) + read_decision date check; FTD/IBD ≤20h for shorts; coach staleness/all-missing=CASH/ceiling cap/No-FTD n/a; only validated candidates arm; screener crash → rc 1. Exposure ceiling now a hard planner limit (--max-exposure-pct, heat gross_exposure_pct; commit 23a099a, user chose enforce 2026-09-24). Reconcile FIXED (commit 0066bce, phase 3): same-day signal only, HOLD excludes, heading direction, geometry, recent-analysis veto, ingest after levels with watchlist levels authoritative; UI mirror.

**VCP screener — FIXED 2026-09-24 (commit 5b8d735, phase 4):** hard trend gates, c5 30%, universe-wide RS, SPY missing/stale → exit 1, current-bar check + date-aligned RS, volume zones by date, universe sidecar vcp_universe_meta.json (TV sectors → SPDR) for vcp+short, metrics_cache newest candles (py + vendor JS). Not fixed: same asc-sort bug in the separate tradingview-mcp-jackson checkout; ZigZag final-contraction miss (PLAUSIBLE); min-atr-pct tight-base skip.

**Swing-short — FIXED (7d8419a, phase 5):** falling-MA200 hard gate, MA200-slope weight, RS maxes at -30%, grade A needs a support break, short branch vetoes recent BUY/HOLD analyses.

**Skills — FIXED (677c93f + 7308df7, phase 6):** CI 55/55, KNOWN_SKIP empty (theme-detector tests → v2 formulas, canslim tests patched the wrong client → live network); pair-trade on FMP /stable + TV-scanner sector fallback (scripts/fmp_data.py); signal-postmortem stable eod path; TV client CDP breaker (TVUnavailableError after 2 CDP failures); bottom-flow frontmatter quoted + hook strict YAML (only when PyYAML importable — system python3 lacks it). Note: pead-screener / earnings-trade-analyzer already use the TV data layer; their fmp_client.py is dead code (audit finding was wrong). Not done: low-score SKILL.md structure for save-note / signals-alerts / ticker-analysis / send-telegram.

**Phase 7 (2026-09-24):** dry-runs of evening-prep/premarket/intraday clean; heat snapshot refreshed. NOT done: 4 CLOSED theses with $1.0 exit (REGN/ZTS/INSM/TTEK) — no real fill prices anywhere in artifacts, needs the user's IB statement; autopilot crontab line still commented — enabling it was denied by the permission classifier, user must uncomment it themselves.

Older items still open: orphaned GTC brackets, multi-day fill detection, HTTP exceptions in order handlers, partial submit_brackets, exposure-coach staleness/all-missing=50/No-FTD=0, market-top 5% rally invalidation, swing-short falling MA200, trader-memory postmortem 5d NEUTRAL. See [[corrupted-theses-exit-price-sentinel]], [[trading-system-profile]], [[autopilot-cron-env-gotchas]].
