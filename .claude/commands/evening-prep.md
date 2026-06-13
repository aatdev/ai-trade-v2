---
description: Запустить evening-prep-слот торгового планировщика
argument-hint: [--dry-run] [--force] [--no-telegram] [--date YYYY-MM-DD]
allowed-tools: Bash(bash scripts/run_trading_schedule.sh:*)
---
Запусти **evening-prep**-слот торгового планировщика:

`bash scripts/run_trading_schedule.sh --slot evening-prep $ARGUMENTS`

- Календарный гейт: в не-торговый день США слот сам пропустится (`rc=0`). Чтобы прогнать принудительно — добавь `--force`. Для безопасного теста — `--dry-run` (не вызывает claude/Telegram).
- Реальный прогон может идти долго (внутренние claude-воркфлоу, таймаут 1800с на шаг) — запускай в фоне и дождись завершения, не прерывай по таймауту инструмента.
- По окончании отчитайся: код возврата и ключевые строки лога (`RUN START`/`RUN END`, отработавшие воркфлоу, что пропущено и почему).
