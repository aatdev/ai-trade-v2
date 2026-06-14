---
layout: default
title: ワークフロー
parent: 日本語
nav_order: 4
lang_peer: /en/workflows/
permalink: /ja/workflows/
---

# ワークフロー
{: .no_toc }

> _このページは `scripts/generate_workflow_docs.py` によって自動生成されます。手動編集しないでください。_

個人トレーダー OS の運用ワークフロー manifest 群です。各ワークフローは使用するスキル・判断ゲート・artifact の流れを順番通りに記述しています。[`workflows/`](https://github.com/tradermonty/claude-trading-skills/tree/main/workflows) 以下の manifest が正本で、本ページはそこから自動生成されます。

**翻訳方針:** 本ページは見出しラベルのみ日本語化しています。manifest 本文（`when_to_run` / `decision_question` / `manual_review` 等）は英語正本をそのまま表示します。本文の日本語化は将来の対応予定です（manifest 側に `*_ja` フィールドを追加するか、別のローカライズ層を設ける方向で検討中）。

---

## ワークフロー一覧

| ワークフロー | 頻度 | 目安(分) | API プロファイル | 難易度 |
|---|---|---|---|---|
| [`core-portfolio-weekly`](#core-portfolio-weekly) — Core Portfolio Weekly | weekly | 60 | mixed | beginner |
| [`market-regime-daily`](#market-regime-daily) — Market Regime Daily | daily | 15 | no-api-basic | beginner |
| [`monthly-performance-review`](#monthly-performance-review) — Monthly Performance Review | monthly | 90 | no-api-basic | intermediate |
| [`short-opportunity-daily`](#short-opportunity-daily) — Short Opportunity Daily | daily | 30 | mixed | advanced |
| [`swing-execution-manage`](#swing-execution-manage) — Swing Execution & Management | daily | 20 | mixed | intermediate |
| [`swing-opportunity-daily`](#swing-opportunity-daily) — Swing Opportunity Daily | daily | 30 | fmp-required | intermediate |
| [`trade-memory-loop`](#trade-memory-loop) — Trade Memory Loop | ad-hoc | 30 | no-api-basic | beginner |
| [`value-research-buy-weekly`](#value-research-buy-weekly) — Value Research & Buy Recommendation (Weekly) | weekly | 75 | mixed | intermediate |

---

## Core Portfolio Weekly {#core-portfolio-weekly}

**`core-portfolio-weekly`** · weekly · ~60 min · mixed · beginner

**実行タイミング:** Once per week, typically on Saturday or Sunday before next week's market open. Reviews long-term holdings, dividend positions, and overall allocation.

**実行してはいけないとき:** Do not run as a daily routine. Daily portfolio churn defeats the long-term framing of this workflow.

**必須スキル:** `ib-portfolio-manager`, `trader-memory-core`

**任意スキル:** `kanchi-dividend-review-monitor`, `value-dividend-screener`, `kanchi-dividend-us-tax-accounting`

**artifact 一覧:**

| Artifact | 生成ステップ | 必須 | 下流ヒント |
|---|---|---|---|
| `holdings_snapshot` | 1 | あり | `monthly-performance-review` |
| `allocation_report` | 2 | あり | — |
| `dividend_review_findings` | 3 | なし | — |
| `rebalance_actions` | 4 | あり | — |
| `weekly_journal_entry` | 5 | あり | — |

**ステップ:**

**ステップ 1: Fetch holdings snapshot** → `ib-portfolio-manager`

- produces: `holdings_snapshot`

**ステップ 2: Review allocation and concentration** （判断ゲート） → `ib-portfolio-manager`

- consumes: `holdings_snapshot`
- produces: `allocation_report`
- **判断:** Are sector and single-name concentrations within target bands? If not, what specific reallocation does the trader propose?

**ステップ 3: Check dividend health (T1-T5 anomaly check)** （任意） → `kanchi-dividend-review-monitor`

- consumes: `holdings_snapshot`
- produces: `dividend_review_findings`

**ステップ 4: Decide rebalance actions** （判断ゲート） → `ib-portfolio-manager`

- consumes: `allocation_report`, `dividend_review_findings`
- produces: `rebalance_actions`
- **判断:** Which rebalance actions (if any) will be executed next week? Confirm explicit buy / sell / hold list with sizing.

**ステップ 5: Journal the weekly review** → `trader-memory-core`

- consumes: `rebalance_actions`
- produces: `weekly_journal_entry`

**手動レビュー:**

- Confirm holdings snapshot reflects the actual brokerage state (Interactive Brokers or CSV).
- Confirm rebalance actions are entered manually at the broker, not auto-executed.
- If dividend_review_findings flags T1-T5 issues, defer additional buys until resolved.

**Journal 出力先:** `trader-memory-core`

---

## Market Regime Daily {#market-regime-daily}

**`market-regime-daily`** · daily · ~15 min · no-api-basic · beginner

**実行タイミング:** Before considering new swing-trade risk for the day. Run before market open or in the first 30 minutes after.

**実行してはいけないとき:** Do not use this output as a standalone buy/sell signal. The exposure_decision is a posture (allow / restrict / cash-priority), not a directive.

**必須スキル:** `market-breadth-analyzer`, `uptrend-analyzer`, `exposure-coach`

**任意スキル:** `market-top-detector`, `macro-regime-detector`, `market-news-analyst`

**artifact 一覧:**

| Artifact | 生成ステップ | 必須 | 下流ヒント |
|---|---|---|---|
| `market_breadth_report` | 1 | あり | `swing-opportunity-daily`, `monthly-performance-review` |
| `uptrend_report` | 2 | あり | — |
| `top_risk_report` | 3 | なし | — |
| `news_context_report` | 4 | なし | — |
| `exposure_decision` | 5 | あり | `swing-opportunity-daily` |

**ステップ:**

**ステップ 1: Analyze market breadth** → `market-breadth-analyzer`

- produces: `market_breadth_report`

**ステップ 2: Analyze uptrend participation** → `uptrend-analyzer`

- produces: `uptrend_report`

**ステップ 3: Check market top risk** （任意） → `market-top-detector`

- produces: `top_risk_report`

**ステップ 4: Review market-moving news** （任意） → `market-news-analyst`

- produces: `news_context_report`

**ステップ 5: Decide exposure posture** （判断ゲート） → `exposure-coach`

- consumes: `market_breadth_report`, `uptrend_report`, `top_risk_report`, `news_context_report`
- produces: `exposure_decision`
- **判断:** Given today's breadth, uptrend participation, and top risk, is new swing trade risk allowed, restricted, or cash-priority?

**手動レビュー:**

- Confirm output is not used as a buy/sell signal.
- Confirm whether exposure should be reduced, unchanged, or increased.
- If exposure_decision is restrictive, defer running swing-opportunity-daily.

**Journal 出力先:** `trader-memory-core`

---

## Monthly Performance Review {#monthly-performance-review}

**`monthly-performance-review`** · monthly · ~90 min · no-api-basic · intermediate

**実行タイミング:** First weekend of each month, reviewing the prior month's closed positions, open thesis health, and process improvements. Closes the Plan -> Trade -> Record -> Review -> Improve loop.

**実行してはいけないとき:** Do not skip this review even in losing months — that is when it matters most. Do not run weekly; the monthly cadence is intentional to filter noise.

**必須スキル:** `trader-memory-core`, `signal-postmortem`

**任意スキル:** `trade-performance-coach`, `backtest-expert`, `dual-axis-skill-reviewer`

**artifact 一覧:**

| Artifact | 生成ステップ | 必須 | 下流ヒント |
|---|---|---|---|
| `monthly_aggregate` | 1 | あり | — |
| `aggregate_postmortem` | 2 | あり | — |
| `monthly_performance_coach_report` | 3 | なし | — |
| `monthly_behavior_patterns` | 3 | なし | — |
| `next_month_operating_rules` | 3 | なし | — |
| `hypothesis_revalidation` | 4 | なし | — |
| `skill_review_findings` | 5 | なし | — |
| `monthly_decision_log` | 6 | あり | — |
| `rule_changes_for_next_month` | 6 | あり | — |
| `skill_improvement_backlog` | 6 | なし | — |

**ステップ:**

**ステップ 1: Aggregate the month's trades and theses** → `trader-memory-core`

- produces: `monthly_aggregate`

**ステップ 2: Pattern-level postmortem across the month** （判断ゲート） → `signal-postmortem`

- consumes: `monthly_aggregate`
- produces: `aggregate_postmortem`
- **判断:** What recurring patterns appear across the month's outcomes? Classify by thesis quality, execution, market environment, and randomness.

**ステップ 3: Coach monthly process, risk, and behavior patterns** （任意） （判断ゲート） → `trade-performance-coach`

- consumes: `monthly_aggregate`, `aggregate_postmortem`
- produces: `monthly_performance_coach_report`, `monthly_behavior_patterns`, `next_month_operating_rules`
- **判断:** Which next-month operating rules should be accepted, modified, deferred, or journaled only?

**ステップ 4: Re-validate hypotheses via backtest** （任意） → `backtest-expert`

- consumes: `aggregate_postmortem`
- produces: `hypothesis_revalidation`

**ステップ 5: Review which skills helped or hurt** （任意） → `dual-axis-skill-reviewer`

- consumes: `aggregate_postmortem`
- produces: `skill_review_findings`

**ステップ 6: Produce decision log and rule changes** （判断ゲート） → `trader-memory-core`

- consumes: `aggregate_postmortem`, `hypothesis_revalidation`, `skill_review_findings`
- produces: `monthly_decision_log`, `rule_changes_for_next_month`, `skill_improvement_backlog`
- **判断:** Based on this month's evidence, what specific rules will change next month? Trade-side rules vs repo-side improvements should stay separate.

**手動レビュー:**

- Distinguish process improvements (rule changes) from outcome accidents (randomness).
- Trade-side rule changes apply to the trader's behavior next month.
- Skill-side improvements are repo-improvement candidates and may or may not be acted on.
- Be willing to delete or downgrade rules that aren't working — not just add new ones.

**最終出力:**

- `monthly_decision_log` — What trades worked / what did not, by category
- `rule_changes_for_next_month` — Adjustments to position sizing, entry rules, regime gates
- `skill_improvement_backlog` — Optional feedback into repo improvement loop (skills / workflows)

**Journal 出力先:** `trader-memory-core`

---

## Short Opportunity Daily {#short-opportunity-daily}

**`short-opportunity-daily`** · daily · ~30 min · mixed · advanced

**実行タイミング:** Only when the market regime is deteriorating and the short side is favored: elevated top-risk score, distribution-day cluster, or a Contraction macro regime. Screens for weak Stage 4 leaders breaking support and builds swing-short entry plans held days-to-weeks. Run before the open or in the first 30 minutes after.

**実行してはいけないとき:** Do not run when ftd-detector has confirmed a Follow-Through Day (the rally is being validated — cover shorts, do not add). Do not run as a standalone bearish screener while breadth is healthy and top-risk is low. The shorting posture is a regime-gated decision, never a standing directive. Never treat any output as an auto-sell-short signal.

**必須スキル:** `market-top-detector`, `exposure-coach`, `swing-short-screener`, `technical-analyst`, `position-sizer`, `trader-memory-core`

**任意スキル:** `ibd-distribution-day-monitor`, `macro-regime-detector`, `ftd-detector`, `downtrend-duration-analyzer`, `market-news-analyst`, `parabolic-short-trade-planner`, `ib-portfolio-manager`

**前提ワークフロー（informational）:**

- `market-regime-daily` が期待する artifact `exposure_decision` — Short-side risk is the mirror of the long exposure decision. A cash-priority or restrictive long posture is exactly the regime where this short workflow is warranted; a risk-on posture means defer it.

**artifact 一覧:**

| Artifact | 生成ステップ | 必須 | 下流ヒント |
|---|---|---|---|
| `top_risk_report` | 1 | あり | `monthly-performance-review` |
| `distribution_report` | 2 | なし | — |
| `macro_regime_report` | 3 | なし | — |
| `ftd_veto_report` | 4 | なし | — |
| `short_posture_decision` | 5 | あり | — |
| `short_candidates` | 6 | あり | — |
| `validated_short_setups` | 7 | あり | — |
| `hold_duration_estimate` | 8 | なし | — |
| `squeeze_risk_report` | 9 | なし | — |
| `position_sizing` | 10 | あり | — |
| `short_trade_plans` | 11 | なし | `trade-memory-loop` |
| `borrow_inventory_check` | 12 | なし | — |
| `short_journal_entry` | 13 | あり | `trade-memory-loop` |

**ステップ:**

**ステップ 1: Score market top risk** → `market-top-detector`

- produces: `top_risk_report`

**ステップ 2: Count distribution days** （任意） → `ibd-distribution-day-monitor`

- produces: `distribution_report`

**ステップ 3: Detect macro regime** （任意） → `macro-regime-detector`

- produces: `macro_regime_report`

**ステップ 4: Follow-Through Day veto check** （任意） → `ftd-detector`

- produces: `ftd_veto_report`

**ステップ 5: Decide short-side posture** （判断ゲート） → `exposure-coach`

- consumes: `top_risk_report`, `distribution_report`, `macro_regime_report`, `ftd_veto_report`
- produces: `short_posture_decision`
- **判断:** Given today's top-risk score, distribution cluster, and macro regime — and with no confirmed Follow-Through Day — is adding short-side swing risk allowed, restricted, or forbidden? If an FTD is confirmed, the answer is forbidden (cover, do not add).

**ステップ 6: Screen for Stage 4 weakness candidates** → `swing-short-screener`

- consumes: `short_posture_decision`
- produces: `short_candidates`

**ステップ 7: Validate weak setups on the chart** （判断ゲート） → `technical-analyst`

- consumes: `short_candidates`
- produces: `validated_short_setups`
- **判断:** Which screened candidates show a clean short setup (failed breakout, breaking a major moving average or base support, lower highs)? Reject names still in a Stage 2 uptrend or with constructive structure.

**ステップ 8: Estimate downtrend hold duration** （任意） → `downtrend-duration-analyzer`

- consumes: `validated_short_setups`
- produces: `hold_duration_estimate`

**ステップ 9: Squeeze / catalyst risk check** （任意） → `market-news-analyst`

- consumes: `validated_short_setups`
- produces: `squeeze_risk_report`

**ステップ 10: Calculate short position size** → `position-sizer`

- consumes: `validated_short_setups`, `hold_duration_estimate`
- produces: `position_sizing`

**ステップ 11: Build short trigger plans** （任意） → `parabolic-short-trade-planner`

- consumes: `validated_short_setups`, `position_sizing`
- produces: `short_trade_plans`

**ステップ 12: Verify borrow availability and SSR** （任意） → `ib-portfolio-manager`

- consumes: `short_trade_plans`
- produces: `borrow_inventory_check`

**ステップ 13: Register short thesis in journal** （判断ゲート） → `trader-memory-core`

- consumes: `position_sizing`, `short_trade_plans`, `squeeze_risk_report`
- produces: `short_journal_entry`
- **判断:** For each surviving candidate, register the short thesis with entry / stop (above the broken level or recent swing high) / cover target. Confirm risk per trade matches position-sizer output, total short exposure is within the exposure-coach ceiling, and borrow is locatable.

**手動レビュー:**

- Confirm ftd-detector shows no fresh Follow-Through Day before adding any short.
- Reject any screener candidate where the weekly/daily structure is not clearly broken.
- Confirm a hard-to-borrow locate exists and check SSR (Rule 201) status at the broker.
- Check squeeze risk — avoid heavily-shorted names with pending bullish catalysts.
- Verify total short exposure is within the exposure-coach ceiling before placing orders.
- All short orders are placed manually at the broker; no auto-execution.

**Journal 出力先:** `trader-memory-core`

---

## Swing Execution & Management {#swing-execution-manage}

**`swing-execution-manage`** · daily · ~20 min · mixed · intermediate

**実行タイミング:** After swing-opportunity-daily has produced a registered thesis with an entry plan (entry / stop / target). Opens the position at the broker, manages it in-trade (trim / trail), and executes the planned exit. Bridges the gap between trade planning and the post-close trade-memory-loop.

**実行してはいけないとき:** Do not run without a registered thesis and entry plan from swing-opportunity-daily. Do not run on a cash-priority / restrictive regime day from market-regime-daily. Do not use to discover new candidates — this workflow only executes plans that already passed the validation and sizing gates.

**必須スキル:** `trader-memory-core`, `ib-portfolio-manager`

**任意スキル:** `position-sizer`

**前提ワークフロー（informational）:**

- `swing-opportunity-daily` が期待する artifact `candidate_journal_entry` — Execution requires a registered thesis with a validated setup, entry, stop, target, and position size. This workflow does not screen or plan.
- `market-regime-daily` が期待する artifact `exposure_decision` — Only open new swing risk when the latest exposure decision is non-restrictive. Re-check the regime before every entry and during management.

**artifact 一覧:**

| Artifact | 生成ステップ | 必須 | 下流ヒント |
|---|---|---|---|
| `live_position_sizing` | 1 | なし | — |
| `active_thesis` | 2 | あり | — |
| `entry_order_confirmation` | 3 | あり | — |
| `position_monitor_report` | 4 | あり | — |
| `position_adjustments` | 5 | なし | — |
| `exit_execution` | 6 | あり | — |
| `closed_thesis_record` | 7 | あり | `trade-memory-loop` |

**ステップ:**

**ステップ 1: Re-check position size at live price** （任意） → `position-sizer`

- produces: `live_position_sizing`

**ステップ 2: Activate thesis and attach position** （判断ゲート） → `trader-memory-core`

- consumes: `live_position_sizing`
- produces: `active_thesis`
- **判断:** Has the regime gate (market-regime-daily) and the entry plan held since planning? Transition the thesis ENTRY_READY -> ACTIVE only if the setup and risk per trade are still valid at the current price.

**ステップ 3: Place entry bracket order** （判断ゲート） → `ib-portfolio-manager`

- consumes: `active_thesis`
- produces: `entry_order_confirmation`
- **判断:** Do the bracket parameters (entry/pivot, stop at base low, 2R target) match the plan, and is total portfolio heat within budget? Place the order manually; confirm the fill and update the thesis with actual price/date.

**ステップ 4: Monitor open position and re-check regime** → `ib-portfolio-manager`

- consumes: `entry_order_confirmation`
- produces: `position_monitor_report`

**ステップ 5: Manage in-trade (trim / trail stop)** （任意） （判断ゲート） → `trader-memory-core`

- consumes: `position_monitor_report`
- produces: `position_adjustments`
- **判断:** At +2R, trim partial and trail the stop to breakeven? If the regime broke (SEVERE distribution / market-top signal) or the setup failed, reduce exposure ahead of the planned exit?

**ステップ 6: Execute planned exit** （判断ゲート） → `ib-portfolio-manager`

- consumes: `position_adjustments`, `position_monitor_report`
- produces: `exit_execution`
- **判断:** Has an exit trigger fired (stop hit, target reached, or setup break)? Execute the exit manually and capture the actual exit price and date.

**ステップ 7: Record exit outcome** → `trader-memory-core`

- consumes: `exit_execution`
- produces: `closed_thesis_record`

**手動レビュー:**

- Confirm market-regime-daily exposure_decision still allows the open risk.
- All orders are placed manually at the broker; no auto-execution.
- Never widen a stop below the original base low to avoid being stopped out.
- Honor the plan — do not improvise targets or sizing mid-trade.
- After closing, run trade-memory-loop on the closed_thesis_record.

**Journal 出力先:** `trader-memory-core`

---

## Swing Opportunity Daily {#swing-opportunity-daily}

**`swing-opportunity-daily`** · daily · ~30 min · fmp-required · intermediate

**実行タイミング:** Only after market-regime-daily has produced a non-restrictive exposure decision. Identifies swing trade candidates and builds entry plans.

**実行してはいけないとき:** Do not run when the latest market-regime-daily exposure_decision is cash-priority or restrictive. Do not use as a standalone screener without the regime gate.

**必須スキル:** `vcp-screener`, `technical-analyst`, `position-sizer`, `trader-memory-core`

**任意スキル:** `canslim-screener`, `breakout-trade-planner`, `theme-detector`

**前提ワークフロー（informational）:**

- `market-regime-daily` が期待する artifact `exposure_decision` — New swing trade risk requires a non-restrictive exposure decision. Skip this workflow on cash-priority or restrictive days.

**artifact 一覧:**

| Artifact | 生成ステップ | 必須 | 下流ヒント |
|---|---|---|---|
| `vcp_candidates` | 1 | あり | — |
| `canslim_candidates` | 2 | なし | — |
| `theme_candidates` | 3 | なし | — |
| `validated_setups` | 4 | あり | — |
| `position_sizing` | 5 | あり | — |
| `trade_plans` | 6 | なし | `trade-memory-loop` |
| `candidate_journal_entry` | 7 | あり | `trade-memory-loop` |

**ステップ:**

**ステップ 1: Run VCP screener** → `vcp-screener`

- produces: `vcp_candidates`

**ステップ 2: Run CANSLIM screener** （任意） → `canslim-screener`

- produces: `canslim_candidates`

**ステップ 3: Theme detection cross-check** （任意） → `theme-detector`

- produces: `theme_candidates`

**ステップ 4: Validate setups on weekly chart** （判断ゲート） → `technical-analyst`

- consumes: `vcp_candidates`, `canslim_candidates`, `theme_candidates`
- produces: `validated_setups`
- **判断:** Which candidates have a clean weekly setup (Stage 2 uptrend, tight base) and pass the manual chart review? Reject candidates that don't.

**ステップ 5: Calculate position size** → `position-sizer`

- consumes: `validated_setups`
- produces: `position_sizing`

**ステップ 6: Build entry plan** （任意） → `breakout-trade-planner`

- consumes: `validated_setups`, `position_sizing`
- produces: `trade_plans`

**ステップ 7: Register thesis in journal** （判断ゲート） → `trader-memory-core`

- consumes: `position_sizing`, `trade_plans`
- produces: `candidate_journal_entry`
- **判断:** For each candidate that survived validation, register the thesis with entry / stop / target. Confirm risk per trade matches position-sizer output and total portfolio heat is within budget.

**手動レビュー:**

- Confirm market-regime-daily exposure_decision allows new risk before acting.
- Reject any candidate where weekly setup is unclear, even if screener passed.
- Verify total portfolio heat is within budget before placing any order.
- All orders are placed manually at the broker; no auto-execution.

**Journal 出力先:** `trader-memory-core`

---

## Trade Memory Loop {#trade-memory-loop}

**`trade-memory-loop`** · ad-hoc · ~30 min · no-api-basic · beginner

**実行タイミング:** Every time a position is closed (full or partial exit). Records the outcome, generates a postmortem, (optionally) coaches process / risk / execution / behavior patterns, and (optionally) re-validates the original hypothesis via backtest.

**実行してはいけないとき:** Do not run before a position is closed — use trader-memory-core directly to update an open thesis instead. Do not skip this loop after a closed trade, even on winners.

**必須スキル:** `trader-memory-core`, `signal-postmortem`

**任意スキル:** `trade-performance-coach`, `backtest-expert`

**artifact 一覧:**

| Artifact | 生成ステップ | 必須 | 下流ヒント |
|---|---|---|---|
| `closed_thesis_record` | 1 | あり | — |
| `postmortem_findings` | 2 | あり | `monthly-performance-review` |
| `performance_coach_report` | 3 | なし | `monthly-performance-review` |
| `next_session_operating_rules` | 3 | なし | `monthly-performance-review` |
| `backtest_validation` | 4 | なし | — |
| `lessons_log_entry` | 5 | あり | `monthly-performance-review` |

**ステップ:**

**ステップ 1: Record closed trade outcome** → `trader-memory-core`

- produces: `closed_thesis_record`

**ステップ 2: Generate postmortem** （判断ゲート） → `signal-postmortem`

- consumes: `closed_thesis_record`
- produces: `postmortem_findings`
- **判断:** What was the root cause of the outcome — thesis quality, execution, market environment, or randomness? Classify and document.

**ステップ 3: Coach process, risk, and behavior patterns** （任意） （判断ゲート） → `trade-performance-coach`

- consumes: `closed_thesis_record`, `postmortem_findings`
- produces: `performance_coach_report`, `next_session_operating_rules`
- **判断:** Which next-session operating rules should the trader accept, modify, defer, or journal only?

**ステップ 4: Re-validate hypothesis via backtest** （任意） → `backtest-expert`

- consumes: `postmortem_findings`
- produces: `backtest_validation`

**ステップ 5: Append lessons to journal** → `trader-memory-core`

- consumes: `postmortem_findings`, `backtest_validation`
- produces: `lessons_log_entry`

**手動レビュー:**

- Be honest about whether the win was thesis-driven or lucky.
- Be honest about whether the loss was thesis-flawed or executed poorly.
- Don't rationalize randomness as either skill or failure.

**Journal 出力先:** `trader-memory-core`

---

## Value Research & Buy Recommendation (Weekly) {#value-research-buy-weekly}

**`value-research-buy-weekly`** · weekly · ~75 min · mixed · intermediate

**実行タイミング:** Weekly, to build a researched long-side buy list from undervalued / quality dividend / growth candidates. Stage 1 screens (fast server-side scan via tradingview-screener; specialized screeners optional), Stage 2 deep-dives each name (fundamentals + valuation + peer comparison + technicals), Stage 3 produces a professional buy recommendation, sizes it, and journals the thesis. Best run after market-regime-daily confirms new long risk is allowed.

**実行してはいけないとき:** Do not treat the output as an auto-buy signal — every recommendation is a decision gate for human judgement. Skip the enrichment steps that need data sources you cannot reach (they are optional and degrade gracefully).

**必須スキル:** `tradingview-screener`, `us-stock-analysis`, `position-sizer`, `trader-memory-core`

**任意スキル:** `value-dividend-screener`, `dividend-growth-pullback-screener`, `canslim-screener`, `earnings-calendar`, `institutional-flow-tracker`, `technical-analyst`

**前提ワークフロー（informational）:**

- `market-regime-daily` が期待する artifact `exposure_decision` — Adding new long-side risk should follow a non-restrictive exposure decision. Run market-regime-daily first on cash-priority days.

**artifact 一覧:**

| Artifact | 生成ステップ | 必須 | 下流ヒント |
|---|---|---|---|
| `screened_candidates` | 1 | あり | — |
| `value_candidates` | 2 | なし | — |
| `dividend_growth_candidates` | 3 | なし | — |
| `canslim_candidates` | 4 | なし | — |
| `earnings_proximity` | 5 | なし | — |
| `deep_research_reports` | 6 | あり | — |
| `peer_comparison` | 7 | あり | — |
| `smart_money_confirmation` | 8 | なし | — |
| `entry_timing` | 9 | なし | — |
| `buy_recommendations` | 10 | あり | `swing-execution-manage`, `trade-memory-loop` |
| `position_sizing` | 11 | あり | — |
| `thesis_journal` | 12 | あり | `trade-memory-loop` |

**ステップ:**

**ステップ 1: Screen the universe (fast server-side scan)** → `tradingview-screener`

- produces: `screened_candidates`

**ステップ 2: Optional specialized value/dividend re-screen** （任意） → `value-dividend-screener`

- consumes: `screened_candidates`
- produces: `value_candidates`

**ステップ 3: Optional dividend-growth pullback screen** （任意） → `dividend-growth-pullback-screener`

- produces: `dividend_growth_candidates`

**ステップ 4: Optional CANSLIM growth screen** （任意） → `canslim-screener`

- produces: `canslim_candidates`

**ステップ 5: Flag earnings proximity for candidates** （任意） → `earnings-calendar`

- consumes: `screened_candidates`, `value_candidates`, `dividend_growth_candidates`, `canslim_candidates`
- produces: `earnings_proximity`

**ステップ 6: Deep-dive each candidate (fundamentals + valuation + technicals)** → `us-stock-analysis`

- consumes: `screened_candidates`, `value_candidates`, `dividend_growth_candidates`, `canslim_candidates`, `earnings_proximity`
- produces: `deep_research_reports`

**ステップ 7: Compare each candidate against same-industry peers** → `us-stock-analysis`

- consumes: `deep_research_reports`
- produces: `peer_comparison`

**ステップ 8: Confirm institutional accumulation (13F)** （任意） → `institutional-flow-tracker`

- consumes: `deep_research_reports`
- produces: `smart_money_confirmation`

**ステップ 9: Confirm weekly-chart entry timing** （任意） → `technical-analyst`

- consumes: `deep_research_reports`
- produces: `entry_timing`

**ステップ 10: Synthesize professional buy recommendation** （判断ゲート） → `us-stock-analysis`

- consumes: `deep_research_reports`, `peer_comparison`, `smart_money_confirmation`, `entry_timing`, `earnings_proximity`
- produces: `buy_recommendations`
- **判断:** For each researched name, is the thesis a BUY now? Confirm it is genuinely undervalued vs intrinsic value AND vs same-industry peers, the fundamentals are durable, smart-money flow is not distributing, no earnings event is imminent, and the chart is not breaking down. Reject anything that fails — a passed screen is not a buy.

**ステップ 11: Size the position by risk** → `position-sizer`

- consumes: `buy_recommendations`
- produces: `position_sizing`

**ステップ 12: Register thesis in journal** （判断ゲート） → `trader-memory-core`

- consumes: `buy_recommendations`, `position_sizing`
- produces: `thesis_journal`
- **判断:** For each BUY that survived the recommendation gate, register the thesis with entry / stop / target / intrinsic-value note. Confirm risk per trade matches position-sizer output and total portfolio heat stays within budget.

**手動レビュー:**

- Confirm market-regime-daily exposure_decision allows new long risk before buying.
- Reject any name where the valuation case is unclear, even if it passed a screen.
- Normalize EPS before judging valuation — a screener P/E can be distorted by one-time items in TTM earnings; verify against company guidance / a multi-year average.
- Read the peer comparison before judging valuation — a multiple is only cheap/expensive relative to industry peers.
- When enrichment steps (earnings/13F/technical) were skipped, note the missing context.
- Verify total portfolio heat is within budget before placing any order.
- All orders are placed manually at the broker; no auto-execution.

**Journal 出力先:** `trader-memory-core`

---
