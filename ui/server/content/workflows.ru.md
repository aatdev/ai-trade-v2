# Воркфлоу — манифесты последовательностей скилов

## Кратко

Воркфлоу — это YAML-манифест в `workflows/`, описывающий упорядоченную цепочку скилов: какой скил на каком шаге, что он `consumes`/`produces`, где гейт решения. Это **канонический** источник мультискиловых процессов: если проза в `README.md`/`CLAUDE.md` расходится с манифестом — прав YAML. Дашборд показывает два ключевых воркфлоу — `market-regime-daily` и `trade-memory-loop`.

> Манифест — это план исполнения, а не авто-раннер. Реально шаги гоняет `claude -p "Выполни workflow ..."` (через слот-раннер или вручную); человек остаётся в петле на гейтах решений.

## Зачем в плане

- **`market-regime-daily`** — ядро Шага 4 (вечерний режим) и Шага 1 (премаркет-ре-чек). Его финальный артефакт — гейт `exposure_decision`, от которого зависит, открываем ли мы новый риск.
- **`trade-memory-loop`** — Шаг 7: запускается на закрытие каждой сделки, фиксирует исход и делает постмортем + урок. Питает месячный обзор (Шаг 9).

## Терминология

- **Шаг (step)** — одна позиция в цепочке; ровно один скил.
- **Гейт решения (decision_gate)** — шаг, где человек обязан ответить на `decision_question` (например: разрешён ли новый риск сегодня).
- **Артефакт** — именованный выход шага; `mynotes_export: true` помечает то, что стоит сохранить в личную базу (след решений).
- **`consumes`** — «прочитать, если есть»: артефакт существует, только если его шаг отработал (для опциональных шагов — обрабатывай отсутствие).

## Как работает

### `market-regime-daily` (ежедневно, ~15 мин)

Оценивает здоровье рынка и выдаёт позицию по экспозиции.

| Шаг | Скил | Опц. | Гейт | Produces |
| --- | --- | --- | --- | --- |
| 1 | market-breadth-analyzer | — | — | market_breadth_report |
| 2 | uptrend-analyzer | — | — | uptrend_report |
| 3 | market-top-detector | да | — | top_risk_report |
| 4 | market-news-analyst | да | — | news_context_report |
| 5 | exposure-coach | — | **да** | exposure_decision |

Обязательные скилы: market-breadth-analyzer, uptrend-analyzer, exposure-coach. Шаг 5 — гейт: «учитывая breadth, участие в аптренде и риск вершины — новый свинг-риск allow / restrict / cash-priority?». Отчёты пишутся в `trading-data/market/` (breadth, uptrend, top, exposure_posture, news), а машиночитаемое решение — в гейт-файл `trading-data/schedule/exposure_decision_<дата>.json`:

```json
{ "workflow": "market-regime-daily", "date": "<дата>",
  "decision": "allow" | "restrict" | "cash-priority",
  "net_exposure_ceiling_pct": <число|null>,
  "rationale": "...", "key_signals": ["..."] }
```

`decision` — машинный enum (всегда по-английски); `rationale`/`key_signals` — по-русски. Нечитаемый/битый гейт трактуется как `restrict` (fail-safe).

### `trade-memory-loop` (на закрытие сделки, ad-hoc, ~30 мин)

Замыкает цикл памяти после закрытия позиции (полного или частичного).

| Шаг | Скил | Опц. | Гейт | Produces |
| --- | --- | --- | --- | --- |
| 1 | trader-memory-core | — | — | closed_thesis_record |
| 2 | signal-postmortem | — | **да** | postmortem_findings |
| 3 | trade-performance-coach | да | да | performance_coach_report, next_session_operating_rules |
| 4 | backtest-expert | да | — | backtest_validation |
| 5 | trader-memory-core | — | — | lessons_log_entry |

Обязательные скилы: trader-memory-core, signal-postmortem. Гейт на Шаге 2 — классифицировать корневую причину исхода (качество тезиса / исполнение / рынок / случайность). Постмортемы и журнал пишутся через trader-memory-core (артефакты постмортема и урока помечены `mynotes_export`). Запускать **только** после закрытия (для открытого тезиса — обновлять напрямую в trader-memory-core) и **не пропускать** даже на прибыльных сделках.

## Команда

Воркфлоу запускаются как headless `claude -p` (относительные пути, `$(date +%F)` для текущей даты):

```bash
# market-regime-daily → отчёты в trading-data/market/ + гейт trading-data/schedule/
claude -p "Выполни workflow market-regime-daily за сегодня: запусти market-breadth-analyzer, uptrend-analyzer и exposure-coach, сохрани отчёты в trading-data/market/ и запиши гейт-файл trading-data/schedule/exposure_decision_$(date +%F).json с решением allow/restrict/cash-priority" --permission-mode bypassPermissions --output-format text

# trade-memory-loop → постмортем + урок (по закрытой сделке)
claude -p "Выполни trade-memory-loop: закрой тезис <thesis_id> в trader-memory-core (выход \$<цена>, причина <stop_hit|target_hit|time_stop>), сгенерируй постмортем и сформулируй один урок" --permission-mode bypassPermissions --output-format text
```

В обычном потоке `market-regime-daily` гоняется автоматически внутри слотов `premarket`/`evening-prep` (см. слот-раннер); ручной вызов выше — фолбэк. Проверить свежий гейт: `cat trading-data/schedule/exposure_decision_$(date +%F).json`.

---

*Исходник: `workflows/market-regime-daily.yaml`, `workflows/trade-memory-loop.yaml`; схема и правила — `workflows/README.md` и `docs/dev/metadata-and-workflow-schema.md`.*
