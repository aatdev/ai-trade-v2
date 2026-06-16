import fs from 'node:fs';
import https from 'node:https';
import os from 'node:os';
import path from 'node:path';
import type { IbHealth } from '@shared/types';

/**
 * Lightweight liveness probe for the bundled IB Gateway, used by the UI to
 * keep a live "is the Gateway up?" indicator without paying for the full
 * `fetch_ib_snapshot.py` round-trip on every poll.
 *
 * It mirrors `check_ib_connection.py`: discover the Gateway runtime session
 * file, read the port it is listening on, then POST the Client Portal
 * auth-status endpoint (self-signed cert on localhost, so verification is
 * disabled). Everything degrades into a structured `{ ok:false, error }` so
 * the client can render the reason instead of throwing.
 *
 * Offline dev / tests: when `TRADING_UI_IB_FIXTURE` points at a snapshot JSON,
 * health is derived from that snapshot's `ok` flag — no network, no Python.
 */

const SESSION_FILENAME = 'gateway-session.json';
const RUNTIME_SUBPATH = path.join('ib-gateway', '.runtime');
const DEFAULT_TIMEOUT_MS = 4000;

/** paper unless IB_PAPER_TRADING is an explicit falsey token. */
function isPaper(): boolean {
  const raw = (process.env.IB_PAPER_TRADING ?? '').trim().toLowerCase();
  return !['0', 'false', 'no', 'off'].includes(raw);
}

function health(partial: Partial<IbHealth>): IbHealth {
  return {
    ok: false,
    reachable: false,
    authenticated: false,
    port: null,
    error: null,
    source: 'live',
    checked_at: new Date().toISOString(),
    ...partial,
  };
}

/** Ordered candidate dirs for the Gateway runtime session (mirrors check_ib_connection.py). */
function candidateRuntimeDirs(projectRoot: string): string[] {
  const roots: string[] = [];
  const env = process.env.IB_GATEWAY_RUNTIME_DIR?.trim();
  if (env) roots.push(env);
  // The bundled MCP writes its session under vendor/interactive-brokers-mcp/.
  roots.push(path.join(projectRoot, 'vendor', 'interactive-brokers-mcp', RUNTIME_SUBPATH));
  roots.push(path.join(projectRoot, RUNTIME_SUBPATH));
  roots.push(path.join(process.cwd(), RUNTIME_SUBPATH));
  roots.push(path.join(os.homedir(), RUNTIME_SUBPATH));
  return [...new Set(roots)];
}

function findSessionFile(projectRoot: string): string | null {
  for (const dir of candidateRuntimeDirs(projectRoot)) {
    const candidate = path.join(dir, SESSION_FILENAME);
    if (fs.existsSync(candidate)) return candidate;
  }
  return null;
}

/** Derive health from a snapshot fixture (used by TRADING_UI_IB_FIXTURE). */
function fixtureHealth(file: string): IbHealth {
  let raw: { ok?: unknown; error?: unknown };
  try {
    raw = JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (e) {
    return health({ source: 'fixture', error: `IB fixture error: ${(e as Error).message}` });
  }
  const ok = raw.ok === true;
  return health({
    ok,
    reachable: ok,
    authenticated: ok,
    source: 'fixture',
    error: ok ? null : typeof raw.error === 'string' ? raw.error : 'IB Gateway недоступен.',
  });
}

interface ProbeResult {
  reachable: boolean;
  authenticated: boolean;
  error: string | null;
}

/** POST the Client Portal auth-status endpoint; never rejects. */
function probeAuth(port: number, timeoutMs: number): Promise<ProbeResult> {
  return new Promise<ProbeResult>((resolve) => {
    let settled = false;
    const finish = (r: ProbeResult) => {
      if (settled) return;
      settled = true;
      resolve(r);
    };

    const req = https.request(
      {
        // The Client Portal Gateway validates the Host header (raw-IP requests
        // are rejected) and returns 403 for requests with no User-Agent, so we
        // mirror what the Python urllib probe sends implicitly.
        host: 'localhost',
        port,
        path: '/v1/api/iserver/auth/status',
        method: 'POST',
        rejectUnauthorized: false,
        timeout: timeoutMs,
        headers: { 'User-Agent': 'trading-ui-ib-health' },
      },
      (res) => {
        let body = '';
        res.on('data', (d) => (body += d));
        res.on('end', () => {
          try {
            const data = JSON.parse(body) as { authenticated?: unknown };
            finish({ reachable: true, authenticated: data.authenticated === true, error: null });
          } catch {
            finish({ reachable: true, authenticated: false, error: 'auth-status: invalid JSON' });
          }
        });
      },
    );
    req.on('timeout', () => {
      req.destroy();
      finish({ reachable: false, authenticated: false, error: `auth-status timed out after ${timeoutMs}ms` });
    });
    req.on('error', (e) => finish({ reachable: false, authenticated: false, error: e.message }));
    req.end();
  });
}

export async function fetchIbHealth(
  projectRoot: string,
  opts: { timeoutMs?: number } = {},
): Promise<IbHealth> {
  const fixture = process.env.TRADING_UI_IB_FIXTURE;
  if (fixture) return fixtureHealth(fixture);

  const sessionPath = findSessionFile(projectRoot);
  if (!sessionPath) {
    return health({ error: 'IB Gateway не запущен (нет gateway-session.json).' });
  }

  let port: number | null = null;
  try {
    const session = JSON.parse(fs.readFileSync(sessionPath, 'utf8')) as { port?: unknown };
    if (typeof session.port === 'number') port = session.port;
  } catch (e) {
    return health({ error: `Не удалось прочитать session-файл: ${(e as Error).message}` });
  }
  if (port == null) {
    return health({ error: 'В session-файле нет порта Gateway.' });
  }

  const probe = await probeAuth(port, opts.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  const reason = probe.authenticated
    ? null
    : probe.reachable
      ? `IB сессия не аутентифицирована${isPaper() ? '' : ' (LIVE)'}. ${probe.error ?? 'Завершите вход в IB Gateway.'}`.trim()
      : `IB Gateway недоступен: ${probe.error ?? 'нет соединения.'}`;

  return health({
    ok: probe.authenticated,
    reachable: probe.reachable,
    authenticated: probe.authenticated,
    port,
    error: reason,
  });
}
