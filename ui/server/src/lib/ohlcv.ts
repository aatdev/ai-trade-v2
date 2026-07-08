import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import type { OhlcvBar, OhlcvResponse } from '@shared/types';
import { PROJECT_ROOT } from '../config';

/**
 * Fetches live OHLCV bars for the candle-chart modal.
 *
 * There is no Node client for the TradingView desktop session, so we shell out
 * to the vendored `tv` CLI (`tv bars <SYMBOL> -n <count> -t <tf>`), which talks
 * to TradingView Desktop over CDP and prints a JSON envelope:
 *
 *   { success, results: [ { symbol, success, resolved, bars: [{time,open,...}] } ] }
 *
 * The CLI (and TradingView Desktop) may be absent, so we always resolve a
 * structured object — `{ ok:false, error }` when the session is unreachable —
 * mirroring fetchIbSnapshot() so the UI renders the reason instead of erroring.
 *
 * Testing / offline dev: set `TRADING_UI_OHLCV_FIXTURE` to a file holding a raw
 * `tv bars` JSON envelope and it is parsed straight from disk, bypassing the CLI.
 */

const DEFAULT_TIMEOUT_MS = 25_000;

/** Find an executable named `name` on PATH, or null. Node has no built-in `which`. */
function findOnPath(name: string): string | null {
  const dirs = (process.env.PATH || '').split(path.delimiter);
  const exts = process.platform === 'win32' ? (process.env.PATHEXT || '.EXE').split(';') : [''];
  for (const dir of dirs) {
    if (!dir) continue;
    for (const ext of exts) {
      const full = path.join(dir, name + ext);
      try {
        fs.accessSync(full, fs.constants.X_OK);
        return full;
      } catch {
        // not executable here — keep looking
      }
    }
  }
  return null;
}

/**
 * Resolve how to invoke the vendored `tv` CLI. Mirrors _resolve_cli() in
 * scripts/lib/tv_client_base.py so the Node server behaves like the Python
 * clients instead of assuming a bare `tv` is always on PATH (the cause of
 * `spawn tv ENOENT`). Order:
 *   1. explicit override (TRADING_UI_TV_BIN or TV_CLI) — used as the command verbatim
 *   2. a `tv` executable found on PATH
 *   3. `node <repo>/vendor/tradingview-mcp/src/cli/index.js` — the vendored entry
 *      (the package declares a `tv` bin but is not npm-linked onto PATH by default)
 * Returns { cmd, prefixArgs } to spawn, or null when nothing resolves.
 */
export function resolveTvCli(
  projectRoot: string = PROJECT_ROOT,
): { cmd: string; prefixArgs: string[] } | null {
  const override = process.env.TRADING_UI_TV_BIN || process.env.TV_CLI;
  if (override) return { cmd: override, prefixArgs: [] };

  const onPath = findOnPath('tv');
  if (onPath) return { cmd: onPath, prefixArgs: [] };

  const nodeCli = path.join(projectRoot, 'vendor', 'tradingview-mcp', 'src', 'cli', 'index.js');
  if (fs.existsSync(nodeCli)) return { cmd: process.execPath, prefixArgs: [nodeCli] };

  return null;
}

export function errorOhlcv(
  symbol: string,
  timeframe: string,
  error: string,
  source: 'live' | 'fixture' = 'live',
): OhlcvResponse {
  return {
    ok: false,
    symbol,
    resolved: null,
    timeframe,
    bars: [],
    error,
    source,
    generated_at: new Date().toISOString(),
  };
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/** Timeframes whose bars carry an intraday time-of-day (so a session applies). */
const INTRADAY_TFS = new Set(['5', '15', '30', '60', '120', '240']);

// Reused formatter — wall-clock in New York, DST-aware, 24h. Bar `time` is the
// bar's OPEN time, so a 5m bar stamped 09:30 ET is the first regular-session bar.
const ET_FMT = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});

/** Minutes since New-York midnight for a Unix-seconds timestamp. */
function etMinutes(timeSec: number): number {
  const parts = ET_FMT.formatToParts(new Date(timeSec * 1000));
  const h = Number(parts.find((p) => p.type === 'hour')?.value);
  const m = Number(parts.find((p) => p.type === 'minute')?.value);
  return h * 60 + m;
}

/**
 * Classify a bar into the US equity session by its New-York wall-clock time:
 * pre 04:00–09:30, regular 09:30–16:00, post 16:00–20:00. Returns null outside
 * those windows (overnight / non-US). Exported for unit testing.
 */
export function classifyBarSession(timeSec: number): 'pre' | 'rth' | 'post' | null {
  const mins = etMinutes(timeSec);
  if (mins >= 4 * 60 && mins < 9 * 60 + 30) return 'pre';
  if (mins >= 9 * 60 + 30 && mins < 16 * 60) return 'rth';
  if (mins >= 16 * 60 && mins < 20 * 60) return 'post';
  return null;
}

/** Stamp each bar with its US session, but only for intraday timeframes. */
function tagSessions(bars: OhlcvBar[], timeframe: string): OhlcvBar[] {
  if (!INTRADAY_TFS.has(timeframe)) return bars;
  for (const b of bars) {
    const s = classifyBarSession(b.time);
    if (s) b.session = s;
  }
  return bars;
}

/** Coerce one raw bar; returns null if any required field is missing. */
function normalizeBar(value: unknown): OhlcvBar | null {
  if (!value || typeof value !== 'object') return null;
  const v = value as Record<string, unknown>;
  const time = num(v.time);
  const open = num(v.open);
  const high = num(v.high);
  const low = num(v.low);
  const close = num(v.close);
  if (time == null || open == null || high == null || low == null || close == null) return null;
  return { time, open, high, low, close, volume: num(v.volume) ?? 0 };
}

/** Ascending, de-duplicated by timestamp (lightweight-charts requires both). */
function cleanBars(raw: unknown): OhlcvBar[] {
  if (!Array.isArray(raw)) return [];
  const bars = raw.map(normalizeBar).filter((b): b is OhlcvBar => b != null);
  bars.sort((a, b) => a.time - b.time);
  const out: OhlcvBar[] = [];
  for (const b of bars) {
    const prev = out[out.length - 1];
    if (prev && prev.time === b.time) out[out.length - 1] = b;
    else out.push(b);
  }
  return out;
}

/**
 * Parse a `tv bars` JSON envelope into an OhlcvResponse. Returns a structured
 * error response (never throws) when the envelope reports failure or is empty.
 */
export function parseTvBars(
  raw: unknown,
  symbol: string,
  timeframe: string,
  source: 'live' | 'fixture',
): OhlcvResponse {
  if (!raw || typeof raw !== 'object') {
    return errorOhlcv(symbol, timeframe, 'tv bars produced no parseable output', source);
  }
  const env = raw as Record<string, unknown>;
  const results = Array.isArray(env.results) ? env.results : [];
  const first = (results[0] ?? null) as Record<string, unknown> | null;
  if (!first) {
    const detail = typeof env.error === 'string' ? env.error : 'no results';
    return errorOhlcv(symbol, timeframe, `tv bars returned no data: ${detail}`, source);
  }
  if (first.success === false) {
    const detail = typeof first.error === 'string' ? first.error : 'symbol fetch failed';
    return errorOhlcv(symbol, timeframe, detail, source);
  }
  const bars = cleanBars(first.bars);
  if (bars.length === 0) {
    return errorOhlcv(symbol, timeframe, 'tv bars returned an empty series', source);
  }
  return {
    ok: true,
    symbol,
    resolved: typeof first.resolved === 'string' ? first.resolved : null,
    timeframe,
    bars: tagSessions(bars, timeframe),
    error: null,
    source,
    generated_at: new Date().toISOString(),
  };
}

function readFixture(file: string, symbol: string, timeframe: string): OhlcvResponse {
  let text: string;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch (e) {
    return errorOhlcv(symbol, timeframe, `OHLCV fixture not found: ${(e as Error).message}`, 'fixture');
  }
  try {
    return parseTvBars(JSON.parse(text), symbol, timeframe, 'fixture');
  } catch (e) {
    return errorOhlcv(symbol, timeframe, `OHLCV fixture is not valid JSON: ${(e as Error).message}`, 'fixture');
  }
}

function parseStdout(stdout: string, symbol: string, timeframe: string): OhlcvResponse {
  const text = stdout.trim();
  if (!text) return errorOhlcv(symbol, timeframe, 'tv bars produced no output');
  // The CLI prints one JSON object; fall back to the last non-empty line in case
  // a banner/disclaimer leaked onto stdout.
  const candidates = [text, text.split(/\r?\n/).filter(Boolean).pop() ?? ''];
  for (const c of candidates) {
    try {
      const res = parseTvBars(JSON.parse(c), symbol, timeframe, 'live');
      if (res.ok) return res;
    } catch {
      /* try next candidate */
    }
  }
  // No ok result — surface the best structured error we can.
  try {
    return parseTvBars(JSON.parse(candidates[0]), symbol, timeframe, 'live');
  } catch {
    return errorOhlcv(symbol, timeframe, `tv bars produced no valid JSON: ${text.slice(0, 200)}`);
  }
}

export async function fetchOhlcv(
  symbol: string,
  timeframe: string,
  count: number,
  opts: { timeoutMs?: number; extended?: boolean } = {},
): Promise<OhlcvResponse> {
  const fixture = process.env.TRADING_UI_OHLCV_FIXTURE;
  if (fixture) return readFixture(fixture, symbol, timeframe);

  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const cli = resolveTvCli();
  if (!cli) {
    return errorOhlcv(
      symbol,
      timeframe,
      'tv CLI not found: run `npm install` in vendor/tradingview-mcp, or set TRADING_UI_TV_BIN / TV_CLI.',
    );
  }
  const args = [...cli.prefixArgs, 'bars', symbol, '-n', String(count), '-t', timeframe];
  // Extended hours (pre/post-market bars) — only meaningful for intraday TFs.
  if (opts.extended) args.push('-x');

  return new Promise<OhlcvResponse>((resolve) => {
    let out = '';
    let err = '';
    let settled = false;
    const finish = (res: OhlcvResponse) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(res);
    };

    const child = spawn(cli.cmd, args, { env: { ...process.env } });

    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      finish(errorOhlcv(symbol, timeframe, `tv bars timed out after ${timeoutMs}ms`));
    }, timeoutMs);

    child.stdout.on('data', (d) => (out += d.toString()));
    child.stderr.on('data', (d) => (err += d.toString()));
    child.on('error', (e) =>
      finish(errorOhlcv(symbol, timeframe, `Failed to run tv CLI (${cli.cmd}): ${e.message}`)),
    );
    child.on('close', () => {
      const res = parseStdout(out, symbol, timeframe);
      if (res.ok || !err.trim()) return finish(res);
      // stderr present and no ok result: prefer the stderr detail.
      finish(errorOhlcv(symbol, timeframe, err.trim().slice(0, 300)));
    });
  });
}
