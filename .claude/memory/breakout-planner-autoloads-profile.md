---
name: breakout-planner-autoloads-profile
description: breakout-trade-planner и sibling-скрипты авто-загружают trading_profile.json — argparse-дефолты мертвы в проде
metadata: 
  node_type: memory
  type: reference
  originSessionId: e6a27222-6ca6-459c-b896-451c28896ad2
---

`skills/breakout-trade-planner/scripts/plan_breakout_trades.py` имеет `--profile` с дефолтом `_default_profile()` → `$TRADING_PROFILE` или `$TRADING_DATE_DIR/trading_profile.json` (строки 118–128), и `parser.set_defaults(**load_profile(pre_args.profile, PLANNER_PROFILE_KEYS))` (строка 864) делает значения профиля эффективными дефолтами (явные CLI-флаги их перекрывают). `PLANNER_PROFILE_KEYS` включает `risk_pct` и `earnings_gate_days`. `.env` задаёт `TRADING_DATE_DIR=trading-data` и `TRADING_PROFILE=trading-data/trading_profile.json`.

Поэтому планировщик, вызванный из `run_trading_schedule.py` как `[PLANNER_SCRIPT, "--input", vcp]` без флагов риска, в проде использует **значения профиля** (`risk_pct:1`, `earnings_gate_days:10`, `max_position_pct:25`), а НЕ argparse-дефолты (0.5 / 0 / 10). Я один раз ошибочно вывел «лонги не earnings-гейтятся» и «риск 0.5%» именно из argparse-дефолтов — это ложь.

**Как проверять правильно:** реальные значения смотреть в `parameters` свежего `trading-data/plans/breakout_trade_plan_*.json`, не по argparse. Тот же механизм авто-загрузки профиля у position-sizer и heat-ledger (общий `KNOWN_PROFILE_KEYS`). Связано: [[trading-system-profile]], [[trading-data-layout]].
