# TradingView Screener — расширение вселенной кандидатов

## Кратко

Превращает критерии скрининга (на естественном языке) в фильтры публичного скан-API TradingView и гоняет их по эндпоинту `scanner.tradingview.com/<market>/scan` — той же вкладке **All Stocks**, что и в вебе. **Ни ключа, ни авторизации, ни TradingView Desktop не нужно** — это открытый HTTP-POST. Доступно ~238 UI-фильтров (фундаментал, теханализ, дивиденды, рост, маржа), пресеты фильтров, пресеты колонок и сортировки. Выдаёт ранжированную таблицу + Markdown/JSON.

> Это «широкоугольный» скринер по **всему рынку США** (NASDAQ/NYSE/AMEX/OTC), а не по S&P 500. В плане он нужен как воронка первого уровня: дать список тикеров, который дальше уточняет vcp-screener.

## Зачем в плане

**Шаг 5.1 — расширение вселенной.** Когда лонг-watchlist от vcp-screener пуст или слишком узок (S&P 500 ничего не дал), TradingView Screener расширяет поиск за пределы индекса — на всю NASDAQ/NYSE. Готовый список тикеров затем скармливается в vcp-screener как кастомная `--universe` для пристрельного VCP-анализа. То есть здесь — грубый отбор по тренду/моментуму/ликвидности, в vcp — точная проверка паттерна.

## Терминология

- **All Stocks tab** — вселенная скринера: обыкновенные + привилегированные акции, депозитарные расписки (DR), не-ETF фонды; только первичные листинги (`is_primary`), без pre-IPO.
- **Фильтр-токен** — компактная запись условия: `field<op>value`. Операторы `>`, `>=`, `<`, `<=`, `=`, `!=`; диапазон `field=lo..hi`; мультивыбор `field=A|B`; суффиксы `K/M/B/T`; справа можно поставить другое поле (`close>SMA200`).
- **Пресет фильтров** (`--filter-preset`) — именованный готовый набор токенов; токены из `--filters` дописываются поверх.
- **Пресет колонок** (`--columns`) — набор столбцов под вкладку TV.
- **Алиасы полей** — человекочитаемые имена (`pe`, `mkt_cap`, `div_yield`, `rsi`, `perf_3m`) → канонические поля сканера.

## Как работает

1. **Сборка payload.** CLI-токены парсятся в JSON-выражения фильтра. К ним всегда добавляются `is_blacklisted=false` и (по умолчанию) `is_primary=true`. Блок `filter2` задаёт вселенную All Stocks.
2. **Запрос.** POST на `scanner.tradingview.com/america/scan` (рынок по умолчанию — `america`). Ретраи с экспоненциальной задержкой на 429/5xx (до 3 попыток).
3. **Вывод.** Markdown-таблица + JSON, `Total matches` (полное число совпадений) и показанные строки. На 0 совпадений — предупреждение (сканер молча игнорирует неизвестные поля → обычно опечатка в имени).

### Доступные пресеты фильтров

В коде сейчас один пресет:

- **`midterm-momentum`** — широкая дневная воронка под свинг 2 недели – 3 месяца. Состав:
  - **Ликвидность:** `close>15`, `mkt_cap>2B`, `avg_volume>750K`
  - **Структура Stage 2:** `close>SMA50`, `close>SMA200`, `SMA50>SMA200`
  - **Моментум:** `perf_3m>10`, `perf_6m>15`

  Пресет также становится `--screen-name` по умолчанию (явный `--screen-name` перебивает).

### Пресеты колонок

`overview`, `performance`, `valuation`, `dividends`, `profitability`, `income`, `balance`, `cashflow`, `technicals` — зеркала вкладок TV. Либо свой список полей через запятую; `--add-columns` дописывает.

### Прочие селекторы

- `--index` — членство в индексе: `sp500`, `nasdaq100`, `dow30`, `russell1000/2000/3000`, `sp100/sp400`, `nasdaqcomposite` или сырой `SYML:...`
- `--sectors` / `--industries` / `--countries` / `--exchanges` — мультивыбор по таксономии TV (для США — `Electronic Technology`, `Finance`, `Health Technology`, … — не `Technology`/`Healthcare`)
- `--analyst-rating` / `--technical-rating` — `strong_buy,buy,neutral,sell,strong_sell` (маппятся в числовые диапазоны `recommendation_mark` / `Recommend.All`)
- `--sort` (используй форму `--sort=-field` для убывания), `--limit` (по умолчанию 50, максимум 500), `--dry-run` (печать payload без сети)

## Команда

Команда из плана (Шаг 5.1, расширение вселенной за S&P 500):

```bash
python3 skills/tradingview-screener/scripts/run_tv_screener.py \
  --filter-preset midterm-momentum \
  --exchanges NASDAQ,NYSE \
  --sort=-perf_3m \
  --limit 60
```

`--output-dir` по умолчанию указывает в `trading-data/screeners/` (берётся `$TRADING_DATE_DIR/screeners`, иначе `reports/`). Выдаёт `tradingview_screener_<name>_<timestamp>.md` / `.json`. Полученные тикеры передаются в vcp-screener как `--universe`.

---

*Полный каталог из 238 фильтров (8 категорий → поля сканера), enum-значения секторов/индексов/рейтингов и точная семантика All Stocks — в `SKILL.md` скила и `references/tradingview_screener_filters.md`.*
