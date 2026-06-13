# Earnings-календарь (TradingView)

## Кратко

> Календарь отчётностей (earnings) через TradingView, без FMP. Скрипт `tv_earnings_calendar.mjs` дёргает публичный scanner-эндпоинт **изнутри страницы tradingview.com** (через CDP), чтобы унаследовать авторизационные cookie, и возвращает список ближайших отчётов в JSON формы FMP `earning_calendar`. Нужен запущенный **TradingView Desktop** (CDP на `:9222`).

В плане используется именно этот vendor-скрипт, **а не** FMP-скил earnings-calendar — данные тянутся из общего слоя TradingView, без платного API-ключа.

## Зачем в плане

**Шаг 8 — earnings-гейт.** Перед формированием сделок нужно знать отчёты на **3 недели вперёд**, чтобы breakout-trade-planner и правила **исключали** кандидатов, у которых earnings попадают в горизонт удержания. Базовое правило системы: **нельзя держать сделку через отчёт** — гэп на earnings непредсказуем и ломает риск-модель (стоп может быть перепрыгнут). Кандидат с близким отчётом либо отбрасывается, либо план переносится на «после earnings».

«**Earnings-гейт**» = проверка-фильтр: тикер из watchlist/плана сверяется с этим календарём; если дата отчёта внутри окна сделки — кандидат блокируется.

## Терминология

- **earnings_release_date** — дата **последнего** отчёта (для PEAD / недавнего прошлого).
- **earnings_release_next_date** — дата **следующего** (предстоящего) отчёта; именно она важна для гейта.
- **bmo / amc** — before market open / after market close; scanner это поле надёжно не отдаёт, поэтому `time` в выводе пустой.

## Как работает

**Вход:** `--from YYYY-MM-DD` и `--to YYYY-MM-DD` (обязательны). Даты переводятся в Unix-секунды (нижняя граница — полночь UTC, верхняя — конец дня).

**Логика.** Scanner хранит на каждый тикер только **последнюю** и **следующую** даты отчёта. Чтобы покрыть и недавнее прошлое, и ближайшее будущее, делается **два** scan-запроса с фильтром `in_range` по полям `earnings_release_date` и `earnings_release_next_date`, результаты объединяются и дедуплицируются по ключу `symbol|date`. Глубокую историю отчётностей так получить нельзя — только окно вокруг текущей даты.

**Транспорт.** POST на `https://scanner.tradingview.com/america/scan` выполняется через CDP **внутри** вкладки tradingview.com (любой `page`-таргет с `tradingview.com`, предпочтительно `/chart/`). Тело уходит без явного `Content-Type` (как `text/plain`) — это «simple request» без CORS-preflight, который scanner не обрабатывает; JSON-тело он всё равно парсит. Так наследуются cookie сессии.

**Колонки запроса:** `name`, `earnings_release_date`, `earnings_release_next_date`, `earnings_per_share_forecast_next_fq`, `revenue_forecast_next_fq`, `exchange`. Диапазон — до 3000 строк, сортировка по дате по возрастанию.

**Выход (stdout, JSON)** формы FMP `earning_calendar`:

```json
{ "earnings": [
  { "date": "2026-06-15", "symbol": "AAPL", "exchange": "NASDAQ",
    "eps": null, "epsEstimated": 1.23,
    "revenue": null, "revenueEstimated": 98000000000, "time": "" }
] }
```

Фактические `eps`/`revenue` scanner в этом наборе не отдаёт — для будущих отчётов это `null`, заполнены только прогнозы (`epsEstimated`, `revenueEstimated`). Записи отсортированы по дате, затем по тикеру.

**Коды выхода:** `2` — не переданы/некорректны даты; `1` — нет открытой вкладки TradingView на `:9222` или ошибка scanner; `0` — успех.

## Команда

Отчёты на 3 недели вперёд от сегодня (earnings-гейт, Шаг 8):

```bash
node vendor/tradingview-mcp/scripts/tv_earnings_calendar.mjs \
  --from $(date +%F) --to $(date -v+21d +%F)
```

`date -v+21d` — синтаксис BSD/macOS. На Linux: `date -d '+21 days' +%F`.

---

*Исходник: `vendor/tradingview-mcp/scripts/tv_earnings_calendar.mjs`.*
