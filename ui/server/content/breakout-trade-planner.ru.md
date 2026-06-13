# Breakout Trade Planner — breakout-планы Минервини из VCP-скрина

## Кратко

Превращает вывод `vcp-screener` в готовые **планы пробойных сделок** по методу Марка Минервини: вход / стоп / цель / количество акций, плюс шаблоны bracket-ордеров Alpaca. Размер позиции считается по **worst-case** цене входа (не по идеальной), риск каждой сделки и совокупный **portfolio heat** жёстко ограничены. API-ключ не нужен — работает с локальным JSON.

> Чистая калькуляция: скрипт ничего не отправляет брокеру. На выходе — план и готовый шаблон ордера, который ты потом ставишь вручную (Шаг 2.3) либо после подтверждения пробоя.

## Зачем в плане

Это **Шаг 5.4** лонг-ветки вечернего прогона (`evening-prep` при гейте `allow`). После того как `vcp-screener` выдал кандидатов и обновился heat по открытым позициям, планировщик строит по top-кандидатам конкретные сделки и сразу режет тех, кто не проходит риск/heat/earnings-гейт. План ложится в `trading-data/plans/`, уровни (pivot/stop/цель/количество) уходят в тезис журнала (Шаг 5.5) и в watchlist на завтра.

## Терминология

- **Pivot** — точка пробоя: максимум последней контракции базы VCP. Цена выше pivot = сигнал на вход.
- **Signal entry** — `pivot × (1 + 0.1%)`: buy-stop триггер чуть выше pivot.
- **Worst-case entry** — `pivot × (1 + 2%)`: потолок, выше которого не гонимся (`--max-chase-pct`). Сайзинг считается именно от него — консервативно.
- **Worst-case risk** — риск в % от worst-entry: `(worst_entry − stop) / worst_entry`. Должен быть ≤ **8%**, иначе кандидат отбрасывается.
- **R** — риск на акцию = `entry − stop`. Цель по умолчанию **2R** (`entry + 2R`), reward/risk = 2:1.
- **Portfolio heat** — суммарный открытый риск портфеля в % от капитала. Лимит **6%** ($9000 при $150k): кандидат, который перебивает потолок, уходит в `deferred`, а не урезается.
- **Bracket-ордер** — один ордер с тремя ногами: вход + take-profit + stop-loss. Стоп ставится одновременно со входом.

## Как работает

### Minervini Gate (кандидат проходит, только если ВСЕ условия)

| Условие | Pre-breakout | Breakout |
| --- | --- | --- |
| `valid_vcp` | True | True |
| рейтинг скринера | good/strong/textbook | good/strong/textbook |
| worst-case risk | ≤ 8.0% | ≤ 8.0% |
| breakout volume | — | True |
| дистанция от pivot | — | ≤ `--max-chase-pct` (2%) |
| текущая цена | — | ≤ worst_entry |

Кандидаты с `valid_vcp` и баллом 60–69 (но не buyable) уходят в **watchlist** (наблюдение), Breakout без объёма/далеко от pivot — в `rejected`.

### Цены и сайзинг (реальные формулы)

1. **Вход/стоп/цель.** `signal_entry = pivot × 1.001`; `worst_entry = pivot × 1.02`; `stop = last_contraction_low × (1 − 1%)` (буфер `--stop-buffer-pct`). Цель = `worst_entry + 2 × (worst_entry − stop)`. Цены округляются к тику Alpaca (2 знака ≥ $1, 4 знака < $1).
2. **Множитель размера по качеству базы:** textbook (балл ≥ 90) → **×1.75**, strong (≥ 80) → **×1.0**, good (≥ 70) → **×0.75**, developing/weak → **×0** (не торгуем). Эффективный риск = `--risk-pct × множитель`.
3. **Количество акций** (fixed-fractional от worst-entry): `риск$ = капитал × эффективный_риск% / 100`; `акции = ⌊риск$ / (worst_entry − stop)⌋`. Затем применяются лимиты — берётся **минимум**: позиция ≤ 25% капитала (`--max-position-pct`), сектор ≤ 30% (`--max-sector-pct`, с учётом уже открытой экспозиции). Если связал лимит — пишется `binding_constraint`; если вышло 0 акций — `constrained`.
4. **Heat-потолок.** Кандидаты сортируются по баллу (сильные первыми); риск каждого добавляется к накопленному. Как только сумма превышает `--max-portfolio-heat-pct` (6%) — кандидат в `deferred`.

> Базовый `--risk-pct` по умолчанию **0.5%**, но из профиля (`trading_profile.json`) подтягивается личное значение (в плане — 1% = $1500). Явный CLI-флаг всегда перебивает профиль.

### Два режима исполнения (шаблоны ордеров Alpaca)

- **`pre_place`** — `stop_limit` bracket: ставится заранее, авто-триггерится buy-stop'ом на `signal_entry`, limit на `worst_entry` (не даёт гнаться). Это шаблон для предпостановки ордеров с вечера.
- **`post_confirm`** — `limit` bracket: отправляется **после** подтверждения пробоя на 5-мин баре (`close > pivot`, close_loc ≥ 0.60, RVOL ≥ 1.5). limit на `worst_entry`.

Оба — `order_class: bracket` с ногами `take_profit.limit_price` и `stop_loss.stop_price`. Breakout-кандидаты (уже пробившие pivot) ордер-шаблон не получают — только `revalidation`-памятку «подтвердить вживую перед ордером».

### Earnings-гейт

С `--earnings-gate-days N` планировщик одним запросом к публичному скринеру TradingView (`scanner.tradingview.com`, без ключа) тянет даты ближайших отчётов и размечает планы полем `earnings_gate` (`pass`/`blocked`/`unknown`). Actionable/revalidation с отчётом в пределах N торговых дней уходят в `blocked_earnings` и **не съедают heat**. Если скринер недоступен — планы остаются живыми с `earnings_gate: "unknown"` и предупреждением `EARNINGS_GATE_DEGRADED` (даты проверить руками). В плане дня используется N=10.

## Команда

```bash
# Шаг 5.4 — план по VCP-кандидатам с учётом текущего heat (профиль даёт капитал/риск/лимиты)
python3 skills/breakout-trade-planner/scripts/plan_breakout_trades.py \
  --input trading-data/screeners/vcp_screener_<дата>.json \
  --current-exposure-json trading-data/journal/heat_<дата>.json \
  --earnings-gate-days 10
```

Выдаёт пару файлов в `trading-data/plans/`: `breakout_trade_plan_<дата>_<время>.json` (планы + шаблоны ордеров) и `.md` (человекочитаемый отчёт). Внутри: `actionable_orders` (готовые к предпостановке), `revalidation`, `watchlist`, `blocked_earnings`, `deferred`/`constrained`/`rejected`, сводный риск и суммарный heat. `--output-dir` указывать не нужно — дефолт уже `trading-data/plans/`.

---

*Полная методология входа (правила Минервини, рейтинг-банды, режимы исполнения) — в `SKILL.md` скила и `references/minervini_entry_rules.md`.*
