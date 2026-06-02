---
name: TradingView Chart Setup — Active Indicators
description: Список активных индикаторов на чарте пользователя и особенности форматирования тикеров
type: reference
---

## Активные индикаторы на дневном чарте (entity IDs сессионные, не кешируются)

- Moving Average Exponential (3 штуки — разные периоды, текущие значения: ~33.53, ~39.05, ~41.56 для ENPH)
- Bollinger Bands (Basis, Upper, Lower)
- Volume
- Volume Delta
- Relative Strength Index (RSI)
- Stochastic (%K, %D)
- Trading Sessions (2 экземпляра)
- Niveles de Opciones (кастомный Pine — уровни опционов)
- Liquidation HeatMap [BigBeluga]
- Average True Range Overlay
- All Chart Patterns (кастомный Pine — автопаттерны)
- Session Volume Profile HD
- Visible Range Volume Profile
- Open Interest
- Liquidations

## Форматы тикеров

- NASDAQ акции: "NASDAQ:ENPH", "NASDAQ:AAPL" — работает надёжно
- NYSE акции: "NYSE:ticker"
- MOEX: "MOEX:PHOR" — работает (chart_get_state показывает символ как "RUS:PHOR", но переключение через "MOEX:PHOR" успешно)
- MOEX тикер в chart_get_state отображается с префиксом "RUS:", а не "MOEX:" — нормальное поведение

## Особенности

- RSI доступен через data_get_study_values без фильтрации
- Stochastic возвращает %K и %D
- Три EMA — разные периоды, значения разные; период неизвестен (определяется из chart_get_state по entity ID, но он сессионный)
- Pine-индикаторы (Niveles de Opciones, All Chart Patterns) — данные через data_get_pine_lines / data_get_pine_labels
- data_get_pine_lines вернул 0 результатов для ENPH и MP — кастомные индикаторы не рисуют линии для NYSE/NASDAQ акций (вероятно, только для фьючерсов или crypto)
- data_get_study_values на Daily и Weekly для NYSE:MP вернул только 4 индикатора (2 EMA + Volume + Volume Profile), остальные (RSI, MACD, Stochastic) не попали в ответ — возможно, они в отдельных панелях (panes) и недоступны через data_get_study_values на главном pane
- Visible Range Volume Profile всегда попадает в study_values; EMA показывается с текущим значением

## Наблюдения по NYSE:MP (2026-05-15)

- На Weekly чарте EMA значения: ~$51.79 и ~$38.16 (два индикатора EMA)
- На Daily чарте EMA значения: ~$61.03 и ~$56.95
- Weekly OHLCV 30 баров: range $44.43–$83.99; last $61.20; avg vol 38.6M
- Daily OHLCV 60 баров: range $44.43–$76.80; last $61.27; avg vol 5.9M
