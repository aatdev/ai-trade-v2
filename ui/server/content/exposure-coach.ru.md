# Exposure Coach — итоговый Market Posture и потолок экспозиции

## Кратко

Сводит сигналы breadth / regime / flow в **одностраничный Market Posture**: потолок чистой лонг-экспозиции (% от капитала), bias growth-vs-value, ширину участия broad/narrow и итоговую рекомендацию — «новые входы разрешены» vs «приоритет кэша». Отвечает на главный вопрос перед любым анализом отдельной акции: **«сколько капитала вообще держать в рынке прямо сейчас?»**. Работает на готовых JSON апстрим-скилов, API-ключ для самого синтеза **не нужен** (FMP нужен только если подаётся вход institutional-flow).

> **Net exposure ceiling** — верхний предел доли капитала в акциях; система не покупает выше него. **Posture** — единая «поза» рынка (агрессивная/нейтральная/защитная), из которой следуют и потолок, и разрешение на входы.

## Зачем в плане

**Именно этот скил формирует итог дневного гейта** (воркфлоу `market-regime-daily`, Шаг 1). Он принимает баллы `market-breadth-analyzer`, `uptrend-analyzer`, `macro-regime-detector`, `market-top-detector`, `ftd-detector` и др., считает композит и выдаёт `recommendation`, который маппится в три значения гейта: `NEW_ENTRY_ALLOWED → allow`, `REDUCE_ONLY → restrict`, `CASH_PRIORITY → cash-priority`. На **Шаге 8 (недельная сводка)** сводит картину недели в одну позу. Потолок экспозиции напрямую ограничивает суммарный размер новых сделок.

## Терминология

- **Net exposure ceiling** — максимум допустимой лонг-экспозиции (0–100%); планировщик не превышает его.
- **Posture / recommendation** — `NEW_ENTRY_ALLOWED` (входы ок), `REDUCE_ONLY` (новых нет, режем на силе), `CASH_PRIORITY` (агрессивно в кэш).
- **Bias** — наклон портфеля: GROWTH / VALUE / DEFENSIVE / NEUTRAL.
- **Participation** — ширина участия: BROAD / MODERATE / NARROW.
- **Confidence** — доверие к выводу по полноте входов: HIGH / MEDIUM / LOW.

## Как работает

Вход — JSON апстрим-скилов (любое подмножество; недостающие снижают доверие, но не блокируют). Каждый экстрактор приводит вход к 0–100 в «экспозиционной» ориентации (высокий = безопасно держать лонг): breadth/uptrend/ftd берутся как есть, top-risk **инвертируется** (100 − top_score), regime маппится по имени.

### Веса композита

| Вход | Вес | Критичный |
| ---- | --- | --------- |
| regime | **0.25** | да |
| top_risk | **0.20** | да |
| breadth | **0.15** | да |
| uptrend | **0.15** | — |
| institutional | **0.10** | — |
| sector | **0.05** | — |
| theme | **0.05** | — |
| ftd | **0.05** | — |

Композит = взвешенная сумма по **присутствующим** входам / сумму их весов. **Хайркат:** за каждый отсутствующий критичный вход (regime/top_risk/breadth) композит −10.

**Regime → балл:** broadening 80, concentration 60, transitional 50, inflationary 40, contraction 20.

### Потолок экспозиции (композит → %)

| Композит | Потолок |
| -------- | ------- |
| ≥80 | 90–100% |
| 65–80 | 70–90% |
| 50–65 | 50–70% |
| 35–50 | 30–50% |
| 20–35 | 10–30% |
| <20 | 0–10% |

### Рекомендация (порядок проверок)

- `CASH_PRIORITY` — если композит < 30 **или** top_risk < 25.
- `REDUCE_ONLY` — если композит < 50 **или** top_risk < 40 **или** отсутствует ≥2 критичных входа.
- иначе `NEW_ENTRY_ALLOWED`.

### Bias, Participation, Confidence

- **Bias:** inflationary→VALUE, contraction→DEFENSIVE; сильная тема (>60) при broadening/concentration→GROWTH; иначе по лидерству секторов и институциональным потокам, иначе NEUTRAL.
- **Participation:** BROAD, если и uptrend, и breadth ≥50 и низкая дисперсия секторов (<0.15); один из двух ≥50 → MODERATE; иначе NARROW.
- **Confidence:** HIGH при ≥6 входах и всех критичных; MEDIUM при ≥4 входах или ≤1 пропущенном критичном; иначе LOW.

## Команда

```bash
python3 skills/exposure-coach/scripts/calculate_exposure.py \
  --breadth   trading-data/market/breadth_latest.json \
  --uptrend   trading-data/market/uptrend_latest.json \
  --regime    trading-data/market/regime_latest.json \
  --top-risk  trading-data/market/top_risk_latest.json \
  --ftd       trading-data/market/ftd_latest.json \
  --output-dir trading-data/market/
```

Все входы опциональны (частичный набор допустим). Выдаёт `exposure_posture_<timestamp>.json` + `.md` в `trading-data/market/`. Внутри: потолок экспозиции %, recommendation, bias, participation, confidence, композит и баллы по входам, списки provided/missing, текстовое обоснование.

---

*Полные правила скоринга и маппинги режим→потолок — в `SKILL.md` скила и `references/exposure_framework.md`, `references/regime_exposure_map.md` (на английском).*
