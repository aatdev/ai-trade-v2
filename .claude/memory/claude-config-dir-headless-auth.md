---
name: claude-config-dir-headless-auth
description: "UI/headless claude-p даёт 401 \"Please run /login\", потому что без CLAUDE_CODE_OAUTH_TOKEN claude-p... нет — потому что без CLAUDE_CONFIG_DIR claude берёт логаут-конфиг ~/.claude; рабочий логин лежит в /Users/alex/Etc/ClaudeSpitch"
metadata: 
  node_type: memory
  type: project
  originSessionId: 33464537-d384-4566-a894-cd60a023db9e
---

`claude-p` спавнит реальный `claude` (`/Users/alex/Projects/Repos/claude-p`, `src/daemon.zig:339`) и драйвит интерактивный TUI; авторизацию делает этот `claude`, **по конфиг-директории**. Какую директорию он берёт, решает `CLAUDE_CONFIG_DIR` (иначе дефолт `~/.claude`).

**Симптом:** UI-анализ тикера (`/actions/analyze-ticker`) и любой headless-запуск через `claude-p` падает на `💬 Please run /login · API Error: 401 Invalid authentication credentials` (текст приходит как контент assistant-сообщения в stream-json; последующие `SIGTERM`/`exited with code null` — это отмена в `ui/server/src/lib/jobs.ts`, не причина).

**Истинная причина:** рабочий логин Claude CLI у пользователя лежит в кастомной директории **`CLAUDE_CONFIG_DIR=/Users/alex/Etc/ClaudeSpitch`** (её ставит сессия Claude Code). UI-сервер (`npm run dev`) запускается **без** `CLAUDE_CONFIG_DIR`, поэтому `claude` уходит в дефолт `~/.claude`, а он **разлогинен** (нет `~/.claude/.credentials.json`). Валидный `CLAUDE_CODE_OAUTH_TOKEN` из `.env` на этом пути **не используется** → 401.

**Воспроизведение (детерминированное):** `env -u CLAUDE_CONFIG_DIR CLAUDE_CODE_OAUTH_TOKEN=<.env> claude-p --model claude-opus-4-8 ... 'OK'` → assistant-текст ровно `Please run /login · API Error: 401`. С `CLAUDE_CONFIG_DIR=/Users/alex/Etc/ClaudeSpitch` → `OK`. Сам токен валиден (curl к `/v1/messages` → 200).

**Ловушка диагностики:** любой тест из Bash внутри сессии Claude Code наследует `CLAUDE_CONFIG_DIR` → claude-p всегда отвечает OK и маскирует баг. Воспроизводить только сняв `CLAUDE_CONFIG_DIR` (и прочие `CLAUDE_CODE_*`), запуская `claude-p` напрямую (не через `bash -lc` — login-shell переэкспортит переменную).

**Why:** проводка `.env`/env в UI исправна (`loadDotEnv`, `jobs.ts:102` мерджит `process.env`), токен валиден — но claude игнорирует токен, когда есть конфиг-директория в состоянии «разлогинен». Решает не keychain и не токен, а **какой конфиг-директорией пользуется `claude`**.

**How to apply:** фикс — прокинуть `CLAUDE_CONFIG_DIR=/Users/alex/Etc/ClaudeSpitch` в репо-`.env`, тогда `loadDotEnv` отдаст его в `claude-p` → child `claude` берёт залогиненный конфиг (нужен рестарт UI-сервера: `loadDotEnv` срабатывает на старте, tsx-watch на изменение `.env` не перезапускается). Альтернатива — залогинить дефолтный `~/.claude` (`claude /login`), но это второй логин. Cron/autopilot ([[autopilot-cron-env-gotchas]]) вероятно ловят тот же 401 — проверять, что и им виден `CLAUDE_CONFIG_DIR`. См. [[claude-p-wrapper-semantics]].
