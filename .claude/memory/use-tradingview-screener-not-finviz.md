---
name: use-tradingview-screener-not-finviz
description: "For stock screening requests always use tradingview-screener, never finviz-screener"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 27a6953d-302f-41e3-9e78-62a88de2ee64
---

Для любых запросов на скрининг акций всегда использовать скилл `tradingview-screener` вместо `finviz-screener` — даже если запрос по формулировке подходит под finviz (общие фразы вроде «найди акции с...», «screen for...»).

**Why:** finviz-screener только строит URL и открывает браузер (полный набор фильтров требует платный FINVIZ Elite); tradingview-screener (создан 2026-06-04 по просьбе пользователя) возвращает результаты прямо в чат + отчёты MD/JSON, без ключей, на данных TradingView, которыми пользователь и так пользуется.

**How to apply:** при триггерах скрининга вызывать tradingview-screener (`skills/tradingview-screener/scripts/run_tv_screener.py`). finviz-screener — только если пользователь явно попросит FinViz. Результаты скрининга по-прежнему сохранять в MyNotes ([[save-screener-results-to-mynotes]]).
