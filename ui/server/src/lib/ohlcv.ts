import { spawn } from 'node:child_process';
import fs from 'node:fs';
import type { OhlcvBar, OhlcvResponse } from '@shared/types';

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

/** The `tv` CLI; overridable for tests / non-standard installs. */
function tvBin(): string {
  return process.env.TRADING_UI_TV_BIN || 'tv';
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
    bars,
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
  opts: { timeoutMs?: number } = {},
): Promise<OhlcvResponse> {
  const fixture = process.env.TRADING_UI_OHLCV_FIXTURE;
  if (fixture) return readFixture(fixture, symbol, timeframe);

  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const bin = tvBin();
  const args = ['bars', symbol, '-n', String(count), '-t', timeframe];

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

    const child = spawn(bin, args, { env: { ...process.env } });

    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      finish(errorOhlcv(symbol, timeframe, `tv bars timed out after ${timeoutMs}ms`));
    }, timeoutMs);

    child.stdout.on('data', (d) => (out += d.toString()));
    child.stderr.on('data', (d) => (err += d.toString()));
    child.on('error', (e) =>
      finish(errorOhlcv(symbol, timeframe, `Failed to run tv CLI (${bin}): ${e.message}`)),
    );
    child.on('close', () => {
      const res = parseStdout(out, symbol, timeframe);
      if (res.ok || !err.trim()) return finish(res);
      // stderr present and no ok result: prefer the stderr detail.
      finish(errorOhlcv(symbol, timeframe, err.trim().slice(0, 300)));
    });
  });
}
