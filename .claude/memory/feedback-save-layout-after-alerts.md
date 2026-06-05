---
name: save-tv-layout-after-alerts
description: После создания алертов в TradingView всегда сохранять layout графика (Save / Cmd+S)
metadata: 
  node_type: memory
  type: feedback
  originSessionId: b901cbb4-4108-4620-bd95-17086b7fe3a4
---

Пользователь попросил (2026-06-05): после создания алертов в TradingView Desktop всегда сохранять график — то есть layout (кнопка Save / Cmd+S), а не скриншот.

**Why:** `create_alerts.mjs` рисует companion-маркер-линии триггеров (`reconcileMarker`), а `delete_alerts.mjs` их снимает — это изменения layout. Без сохранения линии теряются / возвращаются при перезагрузке layout, и график висит в «unsaved changes».

**How to apply:**
- `signals-alerts` скрипты теперь делают это сами: `create_alerts.mjs` сохраняет layout всегда после обработки сигналов, `delete_alerts.mjs` — если снимал маркер-линии; opt-out — `--no-save-layout`; результат — поле `layout_save` в JSON-отчёте.
- В core добавлена `ui.saveLayout()` (`vendor/tradingview-mcp/src/core/ui.js`, зеркалировано в jackson checkout — см. [[tv-cli-shadowing-jackson]]): кнопка Save в хедере → `TradingViewApi.saveChart()` → Cmd+S fallback. MCP-тул `mcp__tradingview__layout_save` зарегистрирован в `src/tools/ui.js` (нужен рестарт MCP-сервера, чтобы он появился).
- При ручном создании алертов через `mcp__tradingview__alert_create` (единичный тикер, вне скриптов) — после создания вызвать `mcp__tradingview__layout_save`, а до рестарта сервера — `mcp__tradingview__ui_keyboard {key: "S", modifiers: ["meta"]}`. Related: [[tradingview-desktop]].
