import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const PROJECT_MARKER = path.join('scripts', 'run_trading_schedule.py');

// Mirrors _RUNTIME_BIN_DIRS / ensure_runtime_path() in run_trading_schedule.py:
// keep spawned subprocesses (claude, node/tv) able to find their executables
// even when the server was started with a minimal PATH.
const RUNTIME_BIN_DIRS = [
  '/opt/homebrew/bin',
  '/opt/homebrew/sbin',
  '/usr/local/bin',
  path.join(os.homedir(), '.local', 'bin'),
];

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

/** Prepend known Homebrew / user-local bin dirs to PATH (idempotent). */
export function ensureRuntimePath(): void {
  const sep = path.delimiter;
  const parts = (process.env.PATH || '').split(sep);
  const missing = RUNTIME_BIN_DIRS.filter((d) => {
    if (parts.includes(d)) return false;
    try {
      return fs.statSync(d).isDirectory();
    } catch {
      return false;
    }
  });
  if (missing.length) process.env.PATH = [...missing, ...parts].join(sep);
}

/**
 * Load the repo `.env` into process.env (without overriding existing values),
 * so spawned `claude`/skill processes see FMP / TELEGRAM / CLAUDE_CODE_OAUTH_TOKEN.
 * Mirrors load_env_file() in run_trading_schedule.py (supports `export VAR=...`).
 */
export function loadDotEnv(projectRoot: string = PROJECT_ROOT): void {
  let text: string;
  try {
    text = fs.readFileSync(path.join(projectRoot, '.env'), 'utf8');
  } catch {
    return;
  }
  for (let line of text.split(/\r?\n/)) {
    line = line.trim();
    if (!line || line.startsWith('#')) continue;
    line = line.replace(/^export\s+/, '');
    const eq = line.indexOf('=');
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    const val = line.slice(eq + 1).trim().replace(/^['"]|['"]$/g, '');
    if (process.env[key] === undefined) process.env[key] = val;
  }
}

/** Model used for headless ticker-analysis runs (override: TRADING_UI_ANALYZE_MODEL). */
export const ANALYZE_MODEL = process.env.TRADING_UI_ANALYZE_MODEL || 'claude-opus-4-8';

/**
 * Resolve an --mcp-config file that registers the vendored TradingView MCP
 * server, so headless `claude -p` ticker-analysis gets the `mcp__tradingview__*`
 * tools (they are not in the user/global MCP config). Override with
 * TRADING_UI_MCP_CONFIG to point at a custom config (e.g. a different checkout).
 * Returns null when the vendored server is absent — the flag is then skipped.
 *
 * The TradingView MCP server still needs TradingView Desktop running with CDP on
 * :9222 (launch via ./run_tw.sh); it connects lazily on the first tool call.
 */
export function resolveMcpConfigPath(projectRoot: string = PROJECT_ROOT): string | null {
  const override = process.env.TRADING_UI_MCP_CONFIG;
  if (override) return path.isAbsolute(override) ? override : path.join(projectRoot, override);

  const serverEntry = path.join(projectRoot, 'vendor', 'tradingview-mcp', 'src', 'server.js');
  if (!fs.existsSync(serverEntry)) return null;

  const config = {
    mcpServers: {
      tradingview: { command: process.execPath, args: [serverEntry] },
    },
  };
  const out = path.join(os.tmpdir(), 'trading-ui-tradingview-mcp.json');
  try {
    fs.writeFileSync(out, JSON.stringify(config, null, 2));
  } catch {
    return null;
  }
  return out;
}
