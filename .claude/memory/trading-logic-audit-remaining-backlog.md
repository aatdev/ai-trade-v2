---
name: trading-logic-audit-remaining-backlog
description: "Verified-but-unfixed findings from the 2026-07-07 and 2026-09-24 audits (selection pipeline, sizing, gating, orders, skill health)"
metadata:
  node_type: memory
  type: project
  originSessionId: 44b17f7b-9efd-48ac-ad1c-34be4b728be2
  modified: 2026-09-24T19:24:56.171Z
---

2026-07-07 audit fixed 11 findings (commit 5dd25f0). 2026-09-24 re-audit (read-only) confirmed nearly all remaining items STILL PRESENT and found new ones. Fix plan phases 1-7 agreed 2026-09-24; phase 1 done, phases 2-7 (gating, reconcile, VCP, swing-short, skills, data/cron) open.

**Operational state (2026-09-24):** autopilot crontab line commented out → no production runs since 2026-07-08. journal/theses holds only 4 files (_index.json rewritten 07-08 09:14, no audit trail; 07-05 monthly counted 78). Tests write to prod `trading-data/logs/trading_schedule.log` (LOG_FILE not monkeypatched); a 07-07 pytest run invalidated ~27 real short theses.

**Sizing / heat — FIXED 2026-09-24 (commit 2fe43fa, phase 1):** worst-fill sizing (tsig.risk_sized_shares), validated-short 1% leak, geometry rejection (rejected_by_geometry), pending GTC brackets reserved in heat (pending_entries), intraday/tap heat fail-closed (≤30h, HEAT_MAX_AGE_HOURS), tap-time gate re-check (gate_ok_for), short-branch heat fail-safe. Note: stale prod heat now blocks UI open-now until a slot/`trader_memory_cli heat` rebuilds it.

**Gating:** gate outcome depends on whether LLM passes optional macro-regime input (never produced daily → REDUCE_ONLY since 06-26); evening run can reuse morning exposure_decision file (run_claude checks existence, not mtime); FTD/IBD/market-top only weekly and `_latest` without age → shorts armed through fresh FTD; exposure ceiling never enforced; validation fails open (rank>3 or failed step → armed); screener crash → rc=0 "no setups"; `_auto_analyze_reconcile` no geometry/freshness/chase/HOLD checks.

**VCP screener:** passes_trend_filter ignores calculator `passed`; RS ranked vs survivors; SPY fail → rs_rank 0 not None; volume zones index wrong bars (volume_pattern_calculator.py:189,219); sector always Unknown with --universe → sector gate dead; no bar-date freshness; os_read_ohlcv asc+2000 latent time bomb (OpenSearch never pruned); vcp_universe.txt from 2026-06-15. Prod: 0 actionable longs in whole period.

**Skills:** FMP v3 dead → pead-screener, earnings-trade-analyzer, pair-trade-screener dead; wrong stable path in signal-postmortem (use `stable/historical-price-eod/full`); run_all_tests rc=1 (navigator snapshot drift + non-hermetic weekly test); theme-detector 22 fails hidden by KNOWN_SKIP; bottom-flow SKILL.md frontmatter invalid YAML; TV screeners hang with no timeout when CDP unreachable.

Older items still open: orphaned GTC brackets, multi-day fill detection, HTTP exceptions in order handlers, partial submit_brackets, exposure-coach staleness/all-missing=50/No-FTD=0, market-top 5% rally invalidation, swing-short falling MA200, trader-memory postmortem 5d NEUTRAL. See [[corrupted-theses-exit-price-sentinel]], [[trading-system-profile]], [[autopilot-cron-env-gotchas]].
