---
name: signals-alerts
description: Создать, удалить или синхронизировать алерты в TradingView Desktop на основе журнала `results/analysis/signals.md`. По каждому сигналу — 5 алертов приоритетного сценария (Trigger / Stop / T1 / T2 / T3). Use whenever the user asks to создать/удалить/обновить алерты по журналу сигналов, синхронизировать TradingView с signals.md, "поставить триггеры из журнала", "снять алерты по тикерам", "пересоздай алерты", "/signals-alerts", "signals alerts sync".
---

# signals-alerts — синхронизация алертов TradingView с журналом

## Назначение

Этот скилл читает `results/analysis/signals.md`, извлекает по каждому блоку приоритетный сценарий (направление + Trigger / Stop / T1 / T2 / T3) и приводит набор алертов в TradingView Desktop к плану — точечно (diff), а не «снести всё и пересоздать».

**Принцип sync — diff:** алерты, совпадающие по `message`, остаются нетронутыми; недостающие — создаются; устаревшие (которых уже нет в плане) — удаляются. Никаких массовых сносов.

Логика разделена на два слоя:

| Слой | Где | Что делает |
|---|---|---|
| **Скилл (LLM)** | этот документ | парсит запрос пользователя (action + список тикеров), выбирает скрипт, формирует параметры, форматирует финальный отчёт |
| **Скрипты (Node + CDP)** | `scripts/` | тяжёлая работа в TradingView через единое CDP-соединение, без оверхеда на MCP tool-calls |

Это и есть «ускорение»: один запуск скрипта = одно CDP-соединение и последовательная обработка всего плана, вместо десятков отдельных `mcp__tradingview__*` вызовов.

## Когда триггериться

- «создай алерты по signals.md», «поставь триггеры из журнала», «синхронизируй алерты с журналом»
- «сними все алерты по BSX», «удали мои алерты по LULU», «пересоздай алерты по ABT и MP»
- «sync алерты», «обнови алерты в TradingView из журнала», «обновились уровни — пересоздай»
- запуск через `/signals-alerts ...` (форма сохранена для совместимости)

Не триггериться, если:
- запрос про создание алерта на ОДИН тикер вручную с конкретными уровнями (это — прямой `mcp__tradingview__alert_create`);
- запрос про создание сигнала / запись в `signals.md` (это — `ticker-analysis`).

## Скрипты

```
.claude/skills/signals-alerts/scripts/
├── parse_signals.mjs   # signals.md → JSON-план с готовыми 5 алертами на сигнал
├── create_alerts.mjs   # план (stdin/--file) → создаёт недостающие (с дедупликацией по message)
└── delete_alerts.mjs   # тикеры (--tickers / signals из плана) → удаляет «наши» алерты;
                        # с флагом --keep-from-plan — удаляет только устаревшие (diff)
```

Все три — самодостаточные Node-скрипты, импортируют `src/core/*.js` репозитория напрямую. **Скилл сам их не дублирует — он их вызывает.**

### parse_signals.mjs

```bash
node .claude/skills/signals-alerts/scripts/parse_signals.mjs [--tickers BSX,LULU]
```

Выводит в stdout:
```json
{
  "signals": [
    {
      "ticker": "BSX",
      "direction": "LONG",
      "trigger": 54.5, "trigger_volume": 25000000, "trigger_volume_condition": "Greater Than",
      "stop": 51.9, "t1": 57, "t2": 60, "t3": 64.2,
      "alerts": [
        { "level": "Trigger", "price": 54.5,  "price_condition": "Crossing Up",   "volume": 25000000, "volume_condition": "Greater Than", "message": "BSX: сигнал на покупку (лонг) — Trigger $54.50 + vol > 25M" },
        { "level": "Stop",    "price": 51.9,  "price_condition": "Crossing Down", "message": "BSX: закрытие позиции по стопу (лонг) — Stop $51.90" },
        { "level": "T1",      "price": 57,    "price_condition": "Crossing Up",   "message": "BSX: закрытие позиции по T1 (лонг) — $57.00" },
        { "level": "T2",      "price": 60,    "price_condition": "Crossing Up",   "message": "BSX: закрытие позиции по T2 (лонг) — $60.00" },
        { "level": "T3",      "price": 64.2,  "price_condition": "Crossing Up",   "message": "BSX: закрытие позиции по T3 (лонг) — $64.20" }
      ]
    }
  ],
  "skipped": [{ "ticker": "PLTR", "reason": "нет приоритетного сетапа" }]
}
```

Для **SHORT** направления `price_condition` зеркальный: Trigger/T1/T2/T3 — `Crossing Down`, Stop — `Crossing Up`. Шаблоны `message`: `сигнал на продажу (шорт)`, `закрытие позиции по стопу (шорт)`, `закрытие позиции по TN (шорт)`.

**Multi-condition Trigger (price + volume).** Если в строке `**Trigger для Long/Short:**` указан объёмный фильтр («на объёме > 40M», «при vol > 25M», «на volume >= 5.9M», «на V > 30M»), парсер вытащит абсолютное значение объёма (40M → 40 000 000), определит `volume_condition` (`Greater Than` для `>`/`≥`, `Less Than` для `<`/`≤`) и положит их в Trigger-алерт. `create_alerts.mjs` затем создаст **один** алерт с двумя условиями (price + volume через "Add condition") — а не два отдельных. Suffix `K/M/B` (case-insensitive) поддерживается. Если в Trigger-строке объёма нет либо он не числовой («≥ avg») — Trigger остаётся одноусловным по цене.

В `message` Trigger-алерта добавляется суффикс ` + vol > 40M` — он входит в ключ дедупликации, поэтому смена объёма автоматически пересоздаёт алерт через sync.

Если у сигнала нет одного из обязательных полей (Trigger / Stop / T1) — попадает в `skipped` с указанием причины. T2/T3 — опциональны.

### create_alerts.mjs

```bash
node .claude/skills/signals-alerts/scripts/parse_signals.mjs --tickers BSX,LULU \
  | node .claude/skills/signals-alerts/scripts/create_alerts.mjs
```

Что делает на каждый сигнал из плана:
1. `chart.setSymbol(ticker)` → `chart.setTimeframe("D")`.
2. Дедупликация (если не указан `--no-dedupe`): тащит `alerts.list()`, отфильтровывает по префиксу `TICKER:` в message, и не создаёт повторно тот, чей message уже есть.
3. Для каждого `alert` из плана — `alerts.create({ price, price_condition, message })`. Если у `alert` есть поля `volume` + `volume_condition` (обычно — только у Trigger при наличии объёмного фильтра в `signals.md`), они передаются в тот же вызов — `src/core/alerts.js` сам откроет «Add condition», выберет источник `Vol`, выставит `volume_condition` и впечатает значение через CDP-keystrokes. Результат — **один алерт с двумя условиями**, а не два отдельных. Пауза ~0.9 с между вызовами.
4. При неудаче — один retry с задержкой 2 с; если повторно — `error` в отчёте.
5. **После обработки всех сигналов сохраняет layout графика** (кнопка Save в хедере / Cmd+S) — иначе маркер-линии триггеров и состояние графика останутся в «unsaved changes». Отключается флагом `--no-save-layout`.

Выводит JSON `{ results: [{ ticker, created, skipped, errors }], summary, layout_save }`.

### delete_alerts.mjs

```bash
# purge-режим: снести все «наши» алерты по этим тикерам
node .claude/skills/signals-alerts/scripts/delete_alerts.mjs --tickers BSX,LULU
node parse_signals.mjs --tickers BSX | node delete_alerts.mjs

# diff-режим (для sync): снести только устаревшие — те, чьего message нет в плане
node .claude/skills/signals-alerts/scripts/delete_alerts.mjs --keep-from-plan --file plan.json
```

Что делает:
1. `alerts.list()` → собирает все алерты.
2. Для каждого тикера отбирает «наши» — `message.startsWith("TICKER:")`.
   - **purge** (по умолчанию): удалить все «наши».
   - **diff** (`--keep-from-plan`): удалить только тех, чьего `message` нет в `plan.signals[*].alerts[*].message` для этого тикера; остальных оставить (счётчик `kept`).
3. Если есть что удалять — открывает панель алертов и последовательно кликает `[data-name="alert-delete-button"]` + подтверждает диалогом «Delete». Если удалять нечего — панель не трогает.
4. Никогда не вызывает `alerts.deleteAlerts({ delete_all: true })` — он снесёт ВСЕ алерты пользователя.
5. Если с графика снимались маркер-линии (`markers_removed > 0`) — **сохраняет layout** (иначе удалённые линии вернутся при следующей загрузке layout). Отключается `--no-save-layout`.

Выводит JSON `{ results: [{ ticker, deleted, kept, not_found_in_ui, errors }], summary, mode: "diff"|"purge", layout_save }`.

## Алгоритм работы скилла

### 0. Разбор запроса

Определи **action** и опциональный **список тикеров**.

| Запрос пользователя | action | tickers |
|---|---|---|
| «покажи план», «что будет создано», «list», без аргументов | `list` | все |
| «создай алерты», «поставь триггеры», «create» | `create` | все или явные |
| «удали алерты по BSX», «delete BSX LULU» | `delete` | явные |
| «пересоздай алерты», «обновились уровни — sync», «sync» | `sync` | все или явные |

Дефолт без явного action — `list` (безопасно, ничего не меняет).

### 1. Проверка окружения (для create/delete/sync)

Запусти `mcp__tradingview__tv_health_check`. Если падает — выведи в чат:

> ❌ TradingView Desktop недоступен. Запусти `tv launch` или `./scripts/launch_tv_debug_mac.sh` и попробуй ещё раз.

…и остановись.

Для `list` health-check не нужен — парсинг локальный.

### 2. Парсинг плана

Запусти:
```bash
node .claude/skills/signals-alerts/scripts/parse_signals.mjs [--tickers TICKER1,TICKER2]
```

Сохрани JSON во временный файл `./tmp/signals_plan_YYYYMMDD_HHMMSS.json` (согласно правилам репо — временные файлы в `./tmp/`). Этот файл — единый источник параметров для всех последующих скриптов.

### 3. Действие `list`

Из JSON-плана построй и выведи таблицу:

```markdown
| Ticker | Dir   | Trigger | Stop  | T1    | T2    | T3    | Алертов в плане |
|---|---|---|---|---|---|---|---|
| BSX | LONG | 54.50 | 51.90 | 57.00 | 60.00 | 64.20 | 5 |
| LULU | SHORT | 119.94 | 131.50 | 115.00 | 108.00 | 100.00 | 5 |
```

Если в `skipped` что-то есть — добавь под таблицей блок `### Пропущено` со списком тикеров и причиной. Финал: «Никаких изменений в TradingView не сделано.»

### 4. Действие `delete`

```bash
node .claude/skills/signals-alerts/scripts/delete_alerts.mjs --file ./tmp/signals_plan_*.json
```

Захвати stdout (это JSON-отчёт) и используй для финальной таблицы (см. формат вывода).

### 5. Действие `create`

```bash
cat ./tmp/signals_plan_*.json | node .claude/skills/signals-alerts/scripts/create_alerts.mjs
```

(или `--file ./tmp/signals_plan_*.json` — равнозначно.)

### 6. Действие `sync` (diff, не destroy-and-recreate)

Цель: привести алерты TradingView к плану минимальным числом изменений. Что уже совпадает по `message` — не трогаем; чего не хватает — создаём; что устарело — удаляем.

Два шага подряд (порядок важен — сначала чистим, потом досоздаём):

```bash
# 6.1 — удалить только устаревшие «наши» алерты (которых нет в новом плане)
node .claude/skills/signals-alerts/scripts/delete_alerts.mjs --keep-from-plan --file ./tmp/signals_plan_*.json

# 6.2 — создать недостающие (дедупликация по message пропустит уже существующие)
cat ./tmp/signals_plan_*.json | node .claude/skills/signals-alerts/scripts/create_alerts.mjs
```

Что это даёт:
- алерт с тем же `message` (= тот же уровень, та же цена, то же направление) **не пересоздаётся** — он остаётся живым со своей историей;
- если уровень в `signals.md` изменился, новое сообщение содержит новую цену → старый алерт попадает в «устаревшие» и удаляется на шаге 6.1, а новый создаётся на 6.2;
- алерты по тикерам, которых нет в плане, не затрагиваются.

Объедини оба отчёта в одну таблицу (см. формат). Колонка `kept` из 6.1 — это «без изменений», колонка `skipped` (already exists) из 6.2 — то же самое; в финальном отчёте достаточно одной колонки «Без изм.» = `kept` (если он есть; иначе — `skipped`).

### 7. Формат финального ответа в чат

Один markdown-блок:

```markdown
## /signals-alerts — <action> — YYYY-MM-DD HH:MM

**Журнал:** `reports/analysis/signals.md` (N сигналов прочитано)
**Фильтр тикеров:** ALL | <список>
**TradingView:** ✅ доступен
**Режим:** sync (diff) | create | delete | list
**Layout:** 💾 сохранён (save_button) | ⚠️ не сохранён (<ошибка>) | — (не менялся)

| Ticker | Dir   | Уровни (Tr / Stop / T1 / T2 / T3) | Создано | Удалено | Без изм. | Статус |
|---|---|---|---:|---:|---:|---|
| BSX  | LONG  | 54.50 / 51.90 / 57.00 / 60.00 / 64.20      | 0 | 0 | 5 | ✅ без изменений |
| LULU | SHORT | 119.94 / 131.50 / 115.00 / 108.00 / 100.00 | 2 | 2 | 3 | ✅ T1+T2 обновлены |
| ABT  | LONG  | 88.00 / 84.10 / 91.00 / 94.00 / —          | 4 | 0 | 0 | ✅ создано |

**Итог:** создано X, удалено Y, без изменений Z, пропущено W.
```

Строка **Layout** берётся из поля `layout_save` JSON-отчётов скриптов (`create_alerts.mjs` сохраняет всегда после обработки сигналов; `delete_alerts.mjs` — только если снимал маркер-линии). Если `layout_save.attempted = false` в обоих отчётах — пиши `— (не менялся)`. Если `success = false` — `⚠️ не сохранён` с ошибкой и подсказкой: сохранить вручную Cmd+S или вызвать `mcp__tradingview__layout_save`.

Для `sync` колонка «Без изм.» = сумма `kept` (из delete) + `skipped` (из create) для тикера, но без двойного счёта по одному `message` (kept уже исключает messages, которые сейчас в плане — а create дедуплицирует те же самые messages → значения должны совпадать; используй `kept`).

Если у какого-то тикера есть `errors` из скрипта — добавь блок:

```markdown
### Примечания
- ABT — `⚠️ T2 не создан` (alert_create timeout, retry не помог; создай вручную: $94.00, Crossing Up).
- PLTR — `skip: нет приоритетного сетапа` (в журнале «— (сигнала нет)»).
```

## Жёсткие правила

- **Никогда** не вызывай `alerts.deleteAlerts({ delete_all: true })` (он же `mcp__tradingview__alert_delete delete_all=true`) — это удалит ВСЕ алерты пользователя, включая чужие. Удаление — точечно, через `delete_alerts.mjs`.
- **Не запускай скрипты параллельно** — каждый держит свой CDP-сеанс к TradingView, два конкурентных скрипта будут ломать UI-диалоги друг друга.
- **Сам не делай тех же шагов** через `mcp__tradingview__*`, что делает скрипт — это удваивает работу и тормозит. Скилл вызывает скрипт, ждёт его stdout, форматирует отчёт.
- **Никогда не пиши в `signals.md`** — этот скилл только читает журналда.
- **Никогда не дописывай блок «🔔 Созданные алерты» в `report.md`** конкретного тикера — это делает `ticker-analysis` в момент полного анализа.
- **Временные файлы** (`./tmp/signals_plan_*.json`) — не финальный артефакт; в `reports/` их не клади.

## Связанные файлы

- `reports/analysis/signals.md` — источник сигналов (читается).
- `.claude/skills/ticker-analysis/SKILL.md` (Шаг 5) — каноническая схема «5 алертов на приоритетный сценарий», шаблоны `message`.
- `src/core/alerts.js`, `src/core/chart.js`, `src/core/ui.js`, `src/core/health.js` — функции, которые скрипты импортируют напрямую.
- `feedback_create_alerts.md` (в auto-memory) — почему `alert_create` требует CDP-mouse-click + Input.insertText, и почему удаление — только через UI.

## Примеры

- «покажи план алертов» → action=list, без фильтра.
- «создай алерты по BSX и MP» → action=create, tickers=[BSX, MP].
- «удали все алерты по LULU» → action=delete, tickers=[LULU].
- «пересоздай алерты — журнал обновился» → action=sync, без фильтра.
- «обновились уровни по ABT — sync только его» → action=sync, tickers=[ABT].
