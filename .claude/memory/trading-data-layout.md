---
name: trading-data-layout
description: "Все торговые артефакты живут в $TRADING_DATE_DIR (trading-data/): schedule/market/screeners/plans/journal/analysis/logs; скрипты находят каталоги сами"
metadata: 
  node_type: memory
  type: project
  originSessionId: b89fe906-907e-4852-ba41-de2192acb1c3
---

С 2026-06-10 все личные торговые артефакты репо claude-trading-skills лежат в одном корне `$TRADING_DATE_DIR` (`.env`: `TRADING_DATE_DIR="trading-data"`, путь относительно корня репо; gitignored):

- `trading_profile.json` — риск-профиль (см. [[trading-system-profile]]; `.env` также задаёт `TRADING_PROFILE`)
- `schedule/` — гейты: exposure_decision_*.json, watchlist_*.json, monthly_review_*.json
- `market/` — режим рынка: market_breadth, uptrend_analysis, exposure_posture, market_top, macro_regime, ftd, ibd, market_news
- `screeners/` — vcp_screener_*, tradingview_screener_*, swing_short_screener_*, canslim_*
- `plans/` — breakout-планы, position_sizer_*
- `journal/` — theses/ (state trader-memory-core), postmortems/, portfolio_heat_*, monthly/
- `analysis/` — signals.md + разборы тикеров `<TICKER>/<дата>/` (ticker-analysis)
- `logs/` — trading_schedule.log, autopilot/, autopilot_state.json, autopilot_cron.log
- `archive/` — дотрейдинговая история (до 2026-06-10)

**Why:** раньше артефакты были размазаны по reports/, state/, logs/, monthly/ и корню репо; единый корень упрощает бэкап и навигацию.
**How to apply:** скрипты плана (оркестраторы, скринеры, trader-memory, signals-alerts .mjs) резолвят дефолты сами: env `TRADING_DATE_DIR` → строка в repo `.env` → старый фолбэк (reports/ и т.п.). НЕ передавать `--output-dir/--state-dir/--profile`, если не нужен нестандартный путь. Старые пути reports/schedule, state/theses, logs/autopilot* — НЕ использовать. Crontab автопилота пишет лог в `trading-data/logs/autopilot_cron.log`.
