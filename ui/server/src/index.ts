import path from 'node:path';
import { createApp } from './app';
import { PORT, PROJECT_ROOT, ensureRuntimePath, loadDotEnv, resolveTradingDataDir } from './config';

// Make spawned claude/skill processes self-sufficient (secrets + executables).
// Repo-root `.env` first (FMP / TELEGRAM / OAUTH), then UI-local `ui/.env`
// (PORT, UI_AUTH_* — see ui/.env.example); neither overrides already-set vars.
loadDotEnv(PROJECT_ROOT);
loadDotEnv(path.join(PROJECT_ROOT, 'ui'));
ensureRuntimePath();

const dataDir = resolveTradingDataDir(PROJECT_ROOT);
const app = createApp({ dataDir, projectRoot: PROJECT_ROOT });

// Bind to loopback only — this dashboard can spawn scheduler processes.
app.listen(PORT, '127.0.0.1', () => {
  // eslint-disable-next-line no-console
  console.log(`[trading-ui] http://127.0.0.1:${PORT}`);
  // eslint-disable-next-line no-console
  console.log(`[trading-ui] project root: ${PROJECT_ROOT}`);
  // eslint-disable-next-line no-console
  console.log(`[trading-ui] trading-data: ${dataDir}`);
});
