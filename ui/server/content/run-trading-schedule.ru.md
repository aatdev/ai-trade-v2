# Слот-раннер расписания — ручной фолбэк торгового дня

## Кратко

`scripts/run_trading_schedule.py` — оркестратор торгового расписания (CET). Один запуск исполняет один **слот** плана: `premarket`, `evening-prep`, `intraday`, `weekly`, `monthly`. Делает всю реальную работу: гоняет `claude -p` воркфлоу, детерминированные скринеры, пишет гейт-файлы/watchlist и шлёт дайджесты в Telegram.

> Это тот же движок, который вызывает автопилот. Запускать вручную нужно как **фолбэк**: автопилот не прислал Telegram к ожидаемому времени — дёргаешь нужный слот руками. Дашборд тоже зовёт этот скрипт (кнопка «run-slot», по умолчанию `--dry-run`).

## Зачем в плане

Каждый слот = шаг плана:

| Слот | Шаг | Что делает |
| --- | --- | --- |
| `premarket` | Шаг 1 | Быстрая ре-проверка режима + гейт; напоминание поставить bracket-ордера. |
| `evening-prep` | Шаг 4 | Полный режим (EOD) → скрин → планы → валидация → watchlist + алерты TV. |
| `intraday` | Шаг 3 | Дешёвый чек котировок: сигналы ОТКРОЙ / ЗАКРОЙ / +2R по watchlist и позициям. |
| `weekly` | Шаг 8 | Субботний блок: DD / macro / FTD + market-top, сводка на неделю. |
| `monthly` | Шаг 9 | Месячный обзор результатов (1-е воскресенье). |

Гейт `exposure_decision` (allow / restrict / cash-priority), который пишет `premarket`/`evening-prep`, определяет всё дальше: на `allow` идёт лонг-пайплайн, иначе — шорт-ветка (Шаг 6) или только ведение позиций.

## Терминология

- **Слот** — один шаг дня; выбирается флагом `--slot`.
- **Гейт (exposure_decision)** — JSON-решение по экспозиции на дату. Нечитаемый/битый файл → fail-safe `restrict` (новый риск не открываем).
- **Watchlist** — кандидаты на завтра с уровнями вход/стоп/цель/размер; «свежим» считается, только если построен сегодня или в прошлый торговый день США.
- **Single-run lock** — PID-файл `trading-data/logs/trading_schedule.lock`; гарантирует, что только один реальный прогон рулит единственным графиком TradingView и общим состоянием.
- **TV Desktop** — TradingView Desktop с CDP на `:9222`; нужен вечернему/недельному скрину (живой график, кэш отключён) и синку алертов.

## Как работает

### Календарные гейты

Без `--force` слот пропускается, если день не подходит: `premarket`/`evening-prep`/`intraday` — только в торговый день США (выходные/праздники из встроенного списка пропускаются); `weekly` — только суббота; `monthly` — только 1-е воскресенье месяца. Окна привязаны к US-сессии (ET) и пересчитываются в локальное время, чтобы не плыть в недели рассинхрона DST США/ЕС; учитываются короткие сессии (13:00 ET).

### Слоты подробно

| Слот | Логика |
| --- | --- |
| `premarket` | Обновляет heat (открытые позиции) → `run_regime_gate` (быстрый режим, пишет гейт) → Telegram-вердикт. Предупреждает, если watchlist устарел или TV Desktop недоступен. |
| `evening-prep` | Полный режим + гейт. Чистит непозиционные тезисы противоположной стороны (regime-flip hygiene). На `allow`: heat → vcp-screener (top 10) → breakout-trade-planner → claude-валидация графиков top-3 → watchlist + ingest тезисов → авто-анализ top-3 (ticker-analysis) с reconcile уровней → синк алертов TV. Иначе: шорт-ветка (см. ниже). |
| `intraday` | Без claude. Берёт котировки watchlist + открытых позиций, оценивает сигналы ОТКРОЙ ЛОНГ/ШОРТ, СТОП/у-стопа/+2R, MISSED, пропуски по ёмкости/отчётам. Дедуп через `intraday_signals_state.json` (каждый сигнал — раз в день). Снимает `[WL]`-алерты по сбежавшим (MISSED). |
| `weekly` | Детерминированные скрипты ibd-distribution-day-monitor (QQQ,SPY), macro-regime-detector, ftd-detector → claude добирает ручные входы market-top (50DMA breadth, put/call через WebSearch) и сводит неделю. |
| `monthly` | claude гоняет `monthly-performance-review`: агрегат закрытых тезисов, постмортем, правки правил на следующий месяц. |

### Шорт-ветка (evening-prep при не-`allow`)

Включается только под давлением рынка (top-risk ≥ 41 **или** DD ≥ 3) и при отсутствии свежего подтверждённого FTD. Тогда: swing-short-screener (grade B+, top 10) → фильтр кандидатов с отчётом в ближайшие ~10 т.д. (правило 6.4) → валидация → watchlist + алерты. Иначе шорт-скрин пропускается (fail-safe).

### Single-run lock

Реальный (не `--dry-run`) прогон берёт лок. Если лок держит другой процесс — скрипт выходит с кодом **75** («занято»), не считая это ошибкой. `--dry-run` лок не трогает.

### Флаги

| Флаг | Назначение |
| --- | --- |
| `--slot <slot>` | Обязательный: `premarket` / `evening-prep` / `intraday` / `weekly` / `monthly`. |
| `--dry-run` | Печатает промпты/сообщения, не зовёт claude и Telegram, состояние не меняет, лок не берёт. |
| `--force` | Игнорирует календарный гейт (торговый день / суббота / 1-е вс) и окно интрадея. |
| `--no-telegram` | Не слать Telegram (всё уходит в лог). |
| `--date YYYY-MM-DD` | Переопределить дату прогона. |
| `--timeout <сек>` | Таймаут одного claude-воркфлоу (по умолчанию 1800). |

## Команда

Артефакты пишутся в `trading-data/<bucket>/` (`schedule/` — гейт и watchlist, `market/` — режим/DD/macro/FTD, `screeners/` — vcp/short, `plans/` — планы, `journal/` — heat/тезисы). `--output-dir` указывать не нужно.

```bash
# Шаг 1 — премаркет: режим + гейт + напоминание про ордера
python3 scripts/run_trading_schedule.py --slot premarket

# Шаг 4 — вечерний прогон: режим + скрин + планы + watchlist + алерты
python3 scripts/run_trading_schedule.py --slot evening-prep

# Шаг 8 — недельный блок руками (вне субботы — нужен --force)
python3 scripts/run_trading_schedule.py --slot weekly --force

# Шаг 9 — месячный обзор руками (вне 1-го вс — нужен --force)
python3 scripts/run_trading_schedule.py --slot monthly --force

# Шаг 3 — интрадей-чек тихо, без Telegram (вне окна — нужен --force)
python3 scripts/run_trading_schedule.py --slot intraday --force --no-telegram
```

Выход: код `0` — успех, `1` — ошибка слота, `75` — лок занят (повтори позже). Гейт-файл слотов `premarket`/`evening-prep` — `trading-data/schedule/exposure_decision_<дата>.json`.

---

*Исходник: `scripts/run_trading_schedule.py` (обёртка `scripts/run_trading_schedule.sh`). Слот-команды дашборда: `.claude/commands/{premarket,evening-prep,intraday,weekly,monthly}.md`.*
