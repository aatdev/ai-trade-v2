/**
 * OpenSearch backend for the metrics cache.
 *
 * The cache is dual-written: scripts/collect_russell.js writes the local files
 * (state/metrics/TICKER/, see metrics_store.js) AND mirrors them here. Readers
 * (read_metrics.js, the Python metrics_cache.py) try OpenSearch first and fall
 * back to the local file when OpenSearch is unreachable or has no document.
 *
 * Indices:
 *   my_tw_metrics    — one doc per ticker (_id = safeToken), the full metrics
 *                      snapshot (indicators + fundamentals + price + quote).
 *   my_tw_candles_1d — one doc per candle (_id = safeToken_timeSec): raw daily
 *                      OHLCV, so the bar series can be range-queried.
 *
 * Config (env):
 *   OPENSEARCH_URL       base URL (default http://tw.spitch-dev.ai:9200)
 *   METRICS_OPENSEARCH   set to "0" to disable the OpenSearch path entirely
 *
 * Every function degrades gracefully: on the first network failure a process-wide
 * circuit breaker trips so a down server doesn't get hammered once per ticker, and
 * reads return null (callers then use the local file).
 */

import { safeToken } from './metrics_store.js';

export const OS_BASE = (process.env.OPENSEARCH_URL ?? 'http://tw.spitch-dev.ai:9200').replace(
  /\/+$/,
  ''
);
export const OS_ENABLED = (process.env.METRICS_OPENSEARCH ?? '1') !== '0';

export const IDX_METRICS = 'my_tw_metrics';
export const IDX_CANDLES = 'my_tw_candles_1d';

// Cap a candle search; mirrors MAX_OHLCV_BARS in metrics_store.js (1500) with headroom.
const MAX_CANDLE_HITS = 2000;
const REQUEST_TIMEOUT_MS = 4000;

// Process-wide circuit breaker: once a request fails to connect we stop trying so
// a 2000-ticker loop doesn't pay the timeout on every ticker.
let osDown = false;

/** True when the OpenSearch path should be attempted. */
export function osActive() {
  return OS_ENABLED && !osDown;
}

/**
 * Low-level request. Returns the parsed JSON body, or null on a network/connection
 * failure (which also trips the circuit breaker). HTTP error statuses other than
 * 404/409 throw, so write paths can surface real problems.
 */
async function osRequest(method, path, body, { soft = false } = {}) {
  if (!osActive()) return null;
  const url = `${OS_BASE}${path}`;
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body !== undefined) opts.body = typeof body === 'string' ? body : JSON.stringify(body);

  let res;
  try {
    res = await fetch(url, { ...opts, signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS) });
  } catch (err) {
    // Connection refused / DNS / timeout → trip the breaker and degrade.
    osDown = true;
    if (!soft)
      console.warn(`  OpenSearch unreachable (${url}): ${err.message} — using local files`);
    return null;
  }

  const text = await res.text();
  if (!res.ok && res.status !== 404 && res.status !== 409) {
    if (soft) return null;
    throw new Error(`OpenSearch ${method} ${path} → ${res.status}: ${text.slice(0, 300)}`);
  }
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

// ─── Index management ──────────────────────────────────────────────────────────

let indicesEnsured = false;

/** Create the metrics + candle indices if they don't exist (idempotent, once per run). */
export async function ensureIndices() {
  if (!osActive() || indicesEnsured) return;

  const metricsMapping = {
    mappings: {
      properties: {
        ticker: { type: 'keyword' },
        name: { type: 'text', fields: { keyword: { type: 'keyword' } } },
        sector: { type: 'keyword' },
        source: { type: 'keyword' },
        collected_at: { type: 'date' },
        as_of_date: { type: 'date', format: 'yyyy-MM-dd' },
        bars_count: { type: 'integer' },
        // quote / price / indicators map dynamically (plain numbers + small nested objects).
        // fundamentals is large and irregular (TTM + multi-year history) — store it in
        // _source but don't index it, to avoid a mapping explosion / field conflicts.
        fundamentals: { type: 'object', enabled: false },
      },
    },
  };

  const candleMapping = {
    mappings: {
      properties: {
        ticker: { type: 'keyword' },
        time: { type: 'long' }, // UNIX milliseconds (matches the legacy my_tw_candles_1d)
        date: { type: 'date', format: 'yyyy-MM-dd' },
        open: { type: 'float' },
        high: { type: 'float' },
        low: { type: 'float' },
        close: { type: 'float' },
        volume: { type: 'long' },
        collected_at: { type: 'date' },
      },
    },
  };

  for (const [idx, mapping] of [
    [IDX_METRICS, metricsMapping],
    [IDX_CANDLES, candleMapping],
  ]) {
    const exists = await osRequest('GET', `/${idx}`, undefined, { soft: true });
    if (exists == null) return; // breaker tripped mid-loop
    if (exists?.status === 404 || exists?.error?.type === 'index_not_found_exception') {
      const r = await osRequest('PUT', `/${idx}`, mapping);
      if (r != null) console.log(`  Created OpenSearch index: ${idx}`);
    }
  }
  indicesEnsured = true;
}

// ─── Writes ─────────────────────────────────────────────────────────────────────

/** Upsert the full metrics snapshot for a ticker. Returns true on success. */
export async function writeMetrics(metrics) {
  if (!osActive()) return false;
  const id = safeToken(metrics.ticker);
  const r = await osRequest('PUT', `/${IDX_METRICS}/_doc/${encodeURIComponent(id)}`, metrics, {
    soft: true,
  });
  return r != null;
}

/**
 * Bulk-upsert candles for a ticker (overwrites by _id, so overlap bars on --update
 * are safely rewritten). `bars` are OLDEST-FIRST with UNIX-second `time`. The _id
 * uses seconds and the stored `time` field uses milliseconds, matching the legacy
 * my_tw_candles_1d index. Returns the number of bars sent (0 if the breaker is tripped).
 */
export async function writeCandles(ticker, bars, collectedAt) {
  if (!osActive() || !bars?.length) return 0;
  const tok = safeToken(ticker);
  const lines = [];
  for (const b of bars) {
    const id = `${tok}_${b.time}`; // seconds, as in the legacy index
    lines.push(JSON.stringify({ index: { _index: IDX_CANDLES, _id: id } }));
    lines.push(
      JSON.stringify({
        ticker,
        time: b.time * 1000, // stored as ms (legacy schema)
        date: b.date ?? new Date(b.time * 1000).toISOString().slice(0, 10),
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
        volume: b.volume ?? 0,
        collected_at: collectedAt,
      })
    );
  }
  const r = await osRequest('POST', '/_bulk', lines.join('\n') + '\n', { soft: true });
  if (r == null) return 0;
  const errors = r?.items?.filter((i) => i.index?.error)?.length ?? 0;
  if (errors) console.warn(`    OpenSearch bulk errors: ${errors}`);
  return bars.length - errors;
}

// ─── Reads ───────────────────────────────────────────────────────────────────────

/** Read the metrics snapshot for a ticker from OpenSearch, or null. */
export async function readMetrics(ticker) {
  if (!osActive()) return null;
  const id = safeToken(ticker);
  const r = await osRequest('GET', `/${IDX_METRICS}/_doc/${encodeURIComponent(id)}`, undefined, {
    soft: true,
  });
  return r?.found ? r._source : null;
}

/**
 * Reconstruct the ohlcv.json-shaped doc for a ticker from its candle docs:
 *   { ticker, collected_at, as_of_date, count, bars: [{time,date,o,h,l,c,volume}] }
 * Bars are OLDEST-FIRST, mirroring the local file. Returns null when absent.
 */
export async function readCandlesDoc(ticker) {
  if (!osActive()) return null;
  const r = await osRequest(
    'POST',
    `/${IDX_CANDLES}/_search`,
    {
      size: MAX_CANDLE_HITS,
      query: { term: { ticker } },
      sort: [{ time: 'asc' }],
      _source: ['time', 'date', 'open', 'high', 'low', 'close', 'volume', 'collected_at'],
    },
    { soft: true }
  );
  const hits = r?.hits?.hits;
  if (!hits?.length) return null;

  let collectedAt = null;
  const bars = hits.map((h) => {
    const s = h._source;
    if (s.collected_at && (!collectedAt || s.collected_at > collectedAt))
      collectedAt = s.collected_at;
    return {
      time: Math.round(s.time / 1000), // ms (stored) → UNIX seconds (file/consumer shape)
      date: s.date,
      open: s.open,
      high: s.high,
      low: s.low,
      close: s.close,
      volume: s.volume ?? 0,
    };
  });
  const last = bars[bars.length - 1];
  return {
    ticker,
    collected_at: collectedAt,
    as_of_date: last?.date ?? null,
    count: bars.length,
    bars,
  };
}

/**
 * Distinct tickers that have a metrics doc, as their stored `ticker` field.
 * Returns null on failure so callers can fall back to a directory listing.
 */
export async function listTickers() {
  if (!osActive()) return null;
  const r = await osRequest(
    'POST',
    `/${IDX_METRICS}/_search`,
    { size: 10000, _source: ['ticker'], query: { match_all: {} } },
    { soft: true }
  );
  const hits = r?.hits?.hits;
  if (hits == null) return null;
  return hits.map((h) => h._source?.ticker).filter(Boolean);
}
