# Memory Index

- [Respond in Russian](respond-in-russian.md) — user wants all replies in Russian
- [Save screener results to MyNotes](save-screener-results-to-mynotes.md) — file screener outputs into ~/Documents/MyNotes via save-note skill
- [Vendored TV data layer](vendored-tv-data-layer.md) — TradingView CLI/data now vendored under vendor/tradingview-mcp; repo independent of external checkout
- [TV CLI shadowing](tv-cli-shadowing-jackson.md) — global `tv` on PATH = jackson checkout, shadows vendor/; mirror vendor edits there
- [Ticker analysis → per-ticker MyNotes subdir](ticker-analysis-mynotes-per-ticker-subdir.md) — save ticker analyses under Анализ-тикеров/<TICKER>/ (no Финансы/ prefix)
- [TradingView Chart Setup](reference_tradingview_chart_setup.md) — Активные индикаторы на чарте пользователя, форматы тикеров NASDAQ/NYSE/MOEX
- [Create alerts in TV](feedback_create_alerts.md) — CDP-механика alert_create (multi-condition, React inputs, удаление через UI)
- [Save TV layout after alerts](feedback-save-layout-after-alerts.md) — после создания алертов сохранять layout (Save/Cmd+S); скрипты signals-alerts делают это сами, вручную — mcp layout_save
- [Skill docs nav_order pitfall](skill-docs-navorder-pitfall.md) — generate_skill_docs.py --skill/--overwrite пишут nav_order не так, как ждёт --check; рендерить через модуль с check-семантикой
- [Use tradingview-screener, not finviz](use-tradingview-screener-not-finviz.md) — для скрининга всегда tradingview-screener; finviz-screener только по явной просьбе
- [.claude/skills symlink footgun](claude-skills-symlink-footgun.md) — .claude/skills → ../skills; rm/cp на .claude/skills/<x> бьёт по реальному источнику, синхронизация не нужна
- [Trading system profile](trading-system-profile.md) — $150k, риск 1.5–2%/сделка, heat ≤6%, ≤6 позиций, горизонт 2 нед–3 мес, лонг + шорт в слабом рынке; профиль/каталоги скрипты находят сами
- [Trading data layout](trading-data-layout.md) — все торговые артефакты в $TRADING_DATE_DIR (trading-data/): schedule/market/screeners/plans/journal/analysis/logs
- [Autopilot cron env gotchas](autopilot-cron-env-gotchas.md) — cron-запуск autopilot: PATH чинит ensure_runtime_path(), Claude login требует CLAUDE_CODE_OAUTH_TOKEN в .env (keychain недоступен)
- [claude-pee breaks nested in a Claude session](claude-pee-nested-session-breaks.md) — слэш-команды слотов (/weekly и т.д.) запускаются вложенно → claude-шаг молча no-op (rc=0, пусто), слот рапортует успех