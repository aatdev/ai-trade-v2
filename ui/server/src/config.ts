import fs from 'node:fs';
import path from 'node:path';

const PROJECT_MARKER = path.join('scripts', 'run_trading_schedule.py');

/**
 * Resolve the repository root. Honors TRADING_PROJECT_ROOT, otherwise walks up
 * from the cwd and this module's directory looking for the scheduler script.
 */
export function findProjectRoot(): string {
  const envRoot = process.env.TRADING_PROJECT_ROOT;
  if (envRoot) return path.resolve(envRoot);

  for (const start of [process.cwd(), __dirname]) {
    let dir = start;
    for (let i = 0; i < 12; i++) {
      if (fs.existsSync(path.join(dir, PROJECT_MARKER))) return dir;
      const parent = path.dirname(dir);
      if (parent === dir) break;
      dir = parent;
    }
  }
  // Fallback: src/ (dev) and dist/ (prod) both sit at ui/server/<x>, so the
  // repo root is three levels up.
  return path.resolve(__dirname, '..', '..', '..');
}

export const PROJECT_ROOT = findProjectRoot();

/**
 * Resolve the trading-data directory. Mirrors _resolve_trading_data_dir() in
 * scripts/run_trading_schedule.py: TRADING_DATE_DIR (note: DATE, not DATA), or
 * `trading-data` relative to the repo root.
 */
export function resolveTradingDataDir(projectRoot: string = PROJECT_ROOT): string {
  const raw = process.env.TRADING_DATE_DIR || 'trading-data';
  return path.isAbsolute(raw) ? raw : path.join(projectRoot, raw);
}

export const PORT = Number(process.env.PORT || 4000);
