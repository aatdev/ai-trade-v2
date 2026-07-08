---
name: corrupted-theses-exit-price-sentinel
description: 4 CLOSED theses closed at sentinel $1.0 (false ~99% win) — need manual exit-price correction
metadata: 
  node_type: memory
  type: project
  originSessionId: 0bfb977d-3ebd-48e7-b325-dabc249e8aca
---

Four CLOSED theses in `trading-data/journal/theses/` were closed at a sentinel `exit.actual_price = 1.0`, producing fictitious ~99% wins that poisoned win-rate/expectancy stats (the hand-written `monthly_aggregate_2026-06.json` correctly shows 0% / −1.77R). Found during the 2026-07-07 trading-logic audit.

Affected (with real reference prices from entry/target/stop): `th_regn_pvt_20260618_5ce5` (entry 604.94), `th_zts_pvt_20260618_7841` (entry 76.41), `th_insm_pvt_20260618_fd68` (entry 95.07), `th_ttek_pvt_20260618_aef1` (entry 26.88).

`thesis_store.py close/trim/terminate` now REJECT an exit price >50% off every reference price (override: `--force-price` / `forcePrice:true`), and `doctor` (validate_state) flags these under `implausible_exits`. Run `thesis_store.py --state-dir trading-data/journal/theses doctor` to list them. They still need the REAL exit prices re-recorded (I did not fabricate them). See [[trading-logic-audit-remaining-backlog]].
