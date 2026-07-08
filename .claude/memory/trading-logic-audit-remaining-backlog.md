---
name: trading-logic-audit-remaining-backlog
description: Verified but not-yet-fixed findings from the 2026-07-07 trading-logic audit (order lifecycle + skill scoring)
metadata: 
  node_type: memory
  type: project
  originSessionId: 0bfb977d-3ebd-48e7-b325-dabc249e8aca
---

2026-07-07 trading-logic audit fixed 11 findings (risk-multiplier cap, short sizing 3×→profile, fail-closed exposure, heat_complete+slot caps, --stop/--atr conflict, earnings-gate ET date, restrict default, order-template GTC+cOID, secrets/nested-session env, exit-price sanity C1, comma-price parse C2, degraded-gate suppress opens). All with tests; ruff clean.

**Remaining verified findings (NOT yet fixed)** — order-execution ones touch live-money code in `scripts/watchlist_orders.py` / `run_trading_schedule.py` and were deferred to avoid a rushed change:

- **CRITICAL** orphaned GTC brackets never cancelled when a candidate is gap-gated / regime-flipped / MISSED (`_terminate_offside_theses`, `_premarket_gap_block`, MISSED handler) — a resting buy-stop fills days later under RESTRICT, tracked by nothing.
- **CRITICAL** multi-day fill detection: `check_fills`/`cmd_sync`/`armed_order_tickers` read only today's `pending_orders_<date>.json`; a GTC entry filling on D+1 is invisible (thesis stuck ENTRY_READY, no heat/monitoring).
- **HIGH** HTTP exceptions escape order handlers: `requests.RequestException`/`urllib` not in the `(ConnectionError, LookupError, ValueError)` except tuples; `handle_close` can cancel the protective stop then crash before posting the close → naked position. `cmd_listen` loop has no try/except (dies on Telegram blip).
- **HIGH** partial `submit_brackets` (`ok=False`) drops `order_ids` of live tranches → untracked live bracket.
- **MEDIUM pkg** (#15): re-check exposure gate at order placement time; `heat_ok_for` staleness bound; `_auto_analyze_reconcile` geometry/freshness/chase-side validation; short-branch heat fail-safe mirror.
- **Screener scoring** (#16): VCP `passes_trend_filter` ignores calculator `passed` (RS>70 hard gate); `metrics_cache.os_read_ohlcv` sorts asc+size 2000 (drops newest once >2000 docs); swing-short default screens only first ~100 alphabetical S&P names; Stage-4 lacks falling-MA200; VCP RS percentile re-ranked vs survivors only; c5 25% vs canon 30%; benchmark-fetch failure silently zeroes RS.
- **exposure-coach** (#18): no input staleness detection; regime is a CRITICAL 0.25-weight input the daily flow never produces (chronic −10 haircut); all-inputs-missing → composite 50/ceiling 50%; ceiling ignores CASH_PRIORITY/REDUCE_ONLY override; "No FTD" scored 0 like a failed FTD.
- **market-top** (#19): DD count lacks O'Neil 5%-rally invalidation (overstated up to 5 wks); sentiment underestimates complacency when put/call+margin absent.
- **trader-memory rest** (#20): FMP endpoint dead (use `stable/historical-price-eod/full`, bare-list shape); postmortem hardcodes 5d return→always NEUTRAL; same-day trim/close monotonicity clamp; summary_stats R-multiple + win>0; terminate price-without-date; HOLD-supersede tombstone; register() dedup race.

Full per-finding detail (file:line, failure scenario, fix) is in the audit run. See [[corrupted-theses-exit-price-sentinel]], [[trading-system-profile]].
