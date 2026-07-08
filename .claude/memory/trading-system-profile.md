---
name: trading-system-profile
description: "Подтверждённые параметры среднесрочной торговой системы Алекса (капитал, риск, heat, горизонт, направление, тайм-слоты)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 15117cc9-02da-47b1-8771-2522998c754b
---

Параметры, подтверждённые пользователем 2026-06-09 при сборке среднесрочного workflow:

- Рынок: NASDAQ/NYSE; капитал $150 000 (Alpaca paper).
- Риск на сделку: **0.33%** (~$495), лонг и шорт одинаково — подтверждено пользователем 2026-06-16 «так задумано». История: план 1.5% → профиль 1% (2026-06-12) → снижен до 0.33% (к 2026-06-16, вероятно через новый profile-editor UI). 0.33%×6 позиций ≈ 2% макс-heat, сильно консервативнее cap 6%. НЕ поднимать тревогу «сайзинг занижен» — это намеренно.
- Portfolio heat ≤ 6% ($9 000) «живого» риска, ≤ 6 позиций, сектор ≤ 30%, одна позиция ≤ 25% капитала.
- Горизонт «среднесрок»: 2 недели – 3 месяца (гибрид: свинг-вход по дневному графику, победителей держать по трейлингу EMA21→SMA50).
- Направление: лонг; шорты только в слабом рынке (гейт restrict/cash-priority + market-top Orange или DD-кластер). **С 2026-07-08 шорт в автопланировщике opt-in через env `TRADING_ENABLE_SHORTS` (по умолчанию OFF → система long-only): без флага вечерняя шорт-ветка пропускается и `OPEN_SHORT` не армируется. Управление уже открытыми шортами от флага не зависит. Хелпер `shorts_enabled()` в run_trading_schedule.py; ручная кнопка шорт-скрина в UI и standalone-скилы не гейтятся.**
- Время на рутину: 30–60 мин/день, два слота: вечер после 22:30 CET (после авторана evening-prep) и премаркет ~15:00 CET.
- Режимный блок уже автоматизирован: `scripts/run_trading_schedule.py` (cron/launchd), гейт — `trading-data/schedule/exposure_decision_<date>.json` (allow/restrict/cash-priority).
- Почасовой диспетчер: `scripts/run_trading_autopilot.py` (сам выбирает слот premarket/evening-prep/monthly, per-run логи в `trading-data/logs/autopilot/`, Telegram только для сбоев и смены гейта). Пошаговый план новичка: MyNotes `Финансы/Трейдинг/Торговый план/торговый-план-новичка.md`.

**FMP-подписки НЕТ** — пользователь требует не зависеть от FMP. Весь контур на TradingView (2026-06-09): дневной — vcp/swing-short через общий TV-слой `scripts/lib/tv_client.py`, earnings-гейт планировщика через публичный scanner.tradingview.com, MAE/MFE через `tv_price_adapter.py`; недельный (ibd/ftd/market-top/macro-regime) — тоже общий TV-слой (ключ принимается, но игнорируется; ibd больше не падает без ключа). На FMP остались только скрипты календарей (earnings-calendar `fetch_earnings_fmp.py`, economic-calendar-fetcher) — TV-замена для earnings уже есть: `vendor/tradingview-mcp/scripts/tv_earnings_calendar.mjs`. При доработках предлагать TV-альтернативы, не FMP.

**Why:** дефолты скриптов (risk 0.5%, max-position 10%) не совпадают с профилем; источник истины — `trading_profile.json` (risk_pct=0.33 на 2026-06-16).
**How to apply:** параметры зашиты в gitignored `trading-data/trading_profile.json` (профиль включает и earnings_gate_days 10, и time_stop_trading_days 15). С 2026-06-10 флаги `--profile/--state-dir/--output-dir` больше НЕ нужны: plan_breakout_trades.py / position_sizer.py / `trader_memory_cli.py heat` сами находят профиль и каталоги через `$TRADING_DATE_DIR` ([[trading-data-layout]]). Фактический heat перед новыми входами: `trader_memory_cli.py heat` → его JSON в `--current-exposure-json` планировщика. Скрининг — `run_tv_screener.py --filter-preset midterm-momentum` ([[use-tradingview-screener-not-finviz]]).
