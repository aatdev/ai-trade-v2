/**
 * Resolve the TradingView Desktop CDP endpoint.
 *
 * Precedence: TV_CDP_HOST / TV_CDP_PORT env vars → nearest `.env` walking up
 * from each search dir (cwd first, then this module's dir) → localhost:9222.
 * TV_CDP_HOST may carry a port suffix ("10.0.0.1:9222").
 */
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const DEFAULT_HOST = 'localhost';
const DEFAULT_PORT = 9222;
const KEYS = ['TV_CDP_HOST', 'TV_CDP_PORT'];

function readDotenvKeys(file) {
  const out = {};
  for (const raw of readFileSync(file, 'utf8').split(/\r?\n/)) {
    const m = raw.trim().match(/^(?:export\s+)?(TV_CDP_HOST|TV_CDP_PORT)\s*=\s*(.*)$/);
    if (m) out[m[1]] = m[2].trim().replace(/^['"]|['"]$/g, '');
  }
  return out;
}

function findDotenv(startDir) {
  let dir = startDir;
  for (;;) {
    const f = join(dir, '.env');
    if (existsSync(f)) {
      const vals = readDotenvKeys(f);
      if (KEYS.some(k => vals[k])) return vals;
    }
    const parent = dirname(dir);
    if (parent === dir) return {};
    dir = parent;
  }
}

export function resolveCdpEndpoint({ env = process.env, searchDirs } = {}) {
  const dirs = searchDirs ?? [process.cwd(), dirname(fileURLToPath(import.meta.url))];
  let vals = {};
  if (!env.TV_CDP_HOST || !env.TV_CDP_PORT) {
    for (const d of dirs) {
      vals = findDotenv(d);
      if (KEYS.some(k => vals[k])) break;
    }
  }
  let host = env.TV_CDP_HOST || vals.TV_CDP_HOST || DEFAULT_HOST;
  let port = Number(env.TV_CDP_PORT || vals.TV_CDP_PORT) || DEFAULT_PORT;
  const hp = host.match(/^([^:]+):(\d+)$/);
  if (hp) {
    host = hp[1];
    port = Number(hp[2]);
  }
  return { host, port };
}

export const { host: CDP_HOST, port: CDP_PORT } = resolveCdpEndpoint();
