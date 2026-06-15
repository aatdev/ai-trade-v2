import { spawn } from 'node:child_process';
import fs from 'node:fs';
import type { IbSnapshot } from '@shared/types';

/**
 * Fetches a live, read-only Interactive Brokers snapshot for the "IB" tab.
 *
 * The bundled IB Gateway speaks the Client Portal REST API over a local HTTPS
 * port — there is no Node client for it here — so we shell out to the
 * skill's `fetch_ib_snapshot.py`, which discovers the Gateway session and
 * prints a normalized JSON snapshot. The script always emits a structured
 * object (with `ok:false` + an `error` string when the Gateway is down or
 * unauthenticated), so the UI can render the reason instead of erroring.
 *
 * Testing / offline dev: set `TRADING_UI_IB_FIXTURE` to a snapshot JSON file
 * and the snapshot is read straight from disk, bypassing Python entirely.
 */

const SCRIPT = 'skills/ib-portfolio-manager/scripts/fetch_ib_snapshot.py';
const DEFAULT_TIMEOUT_MS = 30_000;

function pythonBin(): string {
  return process.env.PYTHON_BIN || 'python3';
}

/** paper unless IB_PAPER_TRADING is an explicit falsey token. */
function mode(): string {
  const raw = (process.env.IB_PAPER_TRADING ?? '').trim().toLowerCase();
  return ['0', 'false', 'no', 'off'].includes(raw) ? 'live' : 'paper';
}

export function errorSnapshot(error: string, source: 'live' | 'fixture' = 'live'): IbSnapshot {
  return {
    ok: false,
    generated_at: new Date().toISOString(),
    mode: mode(),
    account_id: null,
    account_ids: [],
    summary: null,
    positions: [],
    orders: [],
    trades: [],
    error,
    source,
  };
}

/** Coerce an arbitrary parsed value into a well-formed IbSnapshot (or null). */
function normalizeSnapshot(
  value: unknown,
  source: 'live' | 'fixture',
  defaultOk: boolean,
): IbSnapshot | null {
  if (!value || typeof value !== 'object') return null;
  const v = value as Record<string, unknown>;
  return {
    ok: typeof v.ok === 'boolean' ? v.ok : defaultOk,
    generated_at: typeof v.generated_at === 'string' ? v.generated_at : new Date().toISOString(),
    mode: typeof v.mode === 'string' ? v.mode : mode(),
    account_id: typeof v.account_id === 'string' ? v.account_id : null,
    account_ids: Array.isArray(v.account_ids) ? v.account_ids.map(String) : [],
    summary: v.summary && typeof v.summary === 'object' ? (v.summary as IbSnapshot['summary']) : null,
    positions: Array.isArray(v.positions) ? (v.positions as IbSnapshot['positions']) : [],
    orders: Array.isArray(v.orders) ? (v.orders as IbSnapshot['orders']) : [],
    trades: Array.isArray(v.trades) ? (v.trades as IbSnapshot['trades']) : [],
    error: typeof v.error === 'string' ? v.error : null,
    source: typeof v.source === 'string' ? v.source : source,
  };
}

function parseSnapshot(stdout: string, source: 'live' | 'fixture'): IbSnapshot | null {
  const text = stdout.trim();
  if (!text) return null;
  // The script prints exactly one JSON object; fall back to the last line in
  // case anything else leaked onto stdout.
  const candidates = [text, text.split(/\r?\n/).filter(Boolean).pop() ?? ''];
  for (const c of candidates) {
    try {
      const snap = normalizeSnapshot(JSON.parse(c), source, false);
      if (snap) return snap;
    } catch {
      /* try next candidate */
    }
  }
  return null;
}

function readFixture(file: string): IbSnapshot {
  let text: string;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch (e) {
    return errorSnapshot(`IB fixture not found: ${(e as Error).message}`, 'fixture');
  }
  try {
    const snap = normalizeSnapshot(JSON.parse(text), 'fixture', true);
    if (snap) return snap;
  } catch (e) {
    return errorSnapshot(`IB fixture is not valid JSON: ${(e as Error).message}`, 'fixture');
  }
  return errorSnapshot('IB fixture is not a JSON object.', 'fixture');
}

export async function fetchIbSnapshot(
  projectRoot: string,
  opts: { timeoutMs?: number } = {},
): Promise<IbSnapshot> {
  const fixture = process.env.TRADING_UI_IB_FIXTURE;
  if (fixture) return readFixture(fixture);

  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  return new Promise<IbSnapshot>((resolve) => {
    let out = '';
    let err = '';
    let settled = false;
    const finish = (snap: IbSnapshot) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(snap);
    };

    const child = spawn(pythonBin(), [SCRIPT], {
      cwd: projectRoot,
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
    });

    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      finish(errorSnapshot(`IB snapshot timed out after ${timeoutMs}ms.`));
    }, timeoutMs);

    child.stdout.on('data', (d) => (out += d.toString()));
    child.stderr.on('data', (d) => (err += d.toString()));
    child.on('error', (e) =>
      finish(errorSnapshot(`Failed to run IB snapshot script (${pythonBin()}): ${e.message}`)),
    );
    child.on('close', () => {
      const snap = parseSnapshot(out, 'live');
      if (snap) return finish(snap);
      const detail = (err || out).trim().slice(0, 300) || 'no output';
      finish(errorSnapshot(`IB snapshot script produced no valid JSON: ${detail}`));
    });
  });
}
