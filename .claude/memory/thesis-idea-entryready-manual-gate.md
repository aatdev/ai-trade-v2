---
name: thesis-idea-entryready-manual-gate
description: "IDEA→ENTRY_READY промоушн — ручной гейт, не автоматика; карточки/кнопка Шага 2 требуют ENTRY_READY"
metadata: 
  node_type: memory
  type: project
  originSessionId: 1e089746-85bc-40fa-9b66-7e1183020082
---

`ingest` (thesis_ingest → thesis_store.register) всегда создаёт тезис строго в `IDEA` — статус прибит в `register()` (`thesis_store.py:537`), входящий игнорируется. Карточки Шага 2 (`watchlist_orders.py send` → `select_cards`/`_entry_ready_index`) и кнопка «Поставить IB-bracket» в UI (`routes/actions.ts:229`) срабатывают **только** для `ENTRY_READY`. В автоматике (вечерний прогон, премаркет, recalc, демон listen) **нет** шага `IDEA → ENTRY_READY` — единственный авто-`transition ENTRY_READY` вшит строкой в OPEN-сигнал Шага 3 и исполняется на триггере сразу с `open-position` (→ ACTIVE).

**Why:** значит промоушн `IDEA → ENTRY_READY` — ручное решение «беру в работу» (CLI `store transition <id> ENTRY_READY` или кнопка перехода в карточке Trader Memory в дашборде). Подтверждено эмпирикой: ROIV-тезис 2026-06-18 повышен вручную (reason '1'), ордера ставились через UI (`source: ui`, `message_id: null`), а не Telegram-карточками премаркета.

**How to apply:** если спрашивают «почему карточки Шага 2 не приходят сами» — потому что тезисы лежат в `IDEA`, их надо повысить. Это задокументировано (2026-06-18, Вариант A) в `ui/server/content/trading-plan.md` — Шаг 2 (блок «Тезис ⇄ ордер — ручной гейт») и заметка про п. 5.5. Направление потока: тезис → ордер, не наоборот; связка watchlist↔тезис держится на `thesis_id`. См. [[trading-data-layout]], [[breakout-planner-autoloads-profile]].
