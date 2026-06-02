#!/usr/bin/env node
/**
 * Read a cached metrics snapshot for a ticker (the fast path for skills).
 *
 *   node scripts/read_metrics.js AAPL
 *
 * Source order: OpenSearch first (indices my_tw_metrics + my_tw_candles_1d), then
 * the local file (state/metrics/TICKER/) if OpenSearch is unreachable or empty.
 * Disable the OpenSearch path with METRICS_OPENSEARCH=0.
 *
 * Prints the metrics payload with a `_cache` block:
 *   { found, fresh, age_days, stale_days, source, ohlcv: { available, count, path } }
 *
 * Exit codes let a caller branch without parsing:
 *   0  → snapshot found AND fresh (≤2 days)  → use the cache
 *   3  → snapshot missing or stale           → fall back to a live fetch
 *
 * The full bar series is NOT inlined (it can be ~1000 bars); read the path in
 * `_cache.ohlcv.path` when you need raw OHLCV. On exit 3, pull live from
 * TradingView (data_get_study_values / data_get_ohlcv / fundamentals_get).
 */

import {
  readMetrics as fileReadMetrics,
  metricsAgeDays,
  isFresh,
  STALE_DAYS,
  readOhlcv as fileReadOhlcv,
  ohlcvPath,
} from './lib/metrics_store.js';
import * as os from './lib/opensearch.js';

const ticker = process.argv[2];
if (!ticker) {
  console.error('Usage: node scripts/read_metrics.js <TICKER>');
  process.exit(2);
}

// OpenSearch first, local file as fallback.
let source = 'opensearch';
let metrics = await os.readMetrics(ticker);
let ohlcv = metrics ? await os.readCandlesDoc(ticker) : null;
if (!metrics) {
  source = 'file';
  metrics = fileReadMetrics(ticker);
  ohlcv = fileReadOhlcv(ticker);
}

const fresh = isFresh(metrics);
const ageDays = metricsAgeDays(metrics);

const payload = {
  ...(metrics ?? { ticker }),
  _cache: {
    found: metrics != null,
    fresh,
    age_days: Number.isFinite(ageDays) ? Number(ageDays.toFixed(2)) : null,
    stale_days: STALE_DAYS,
    source: metrics ? source : null,
    ohlcv: {
      available: ohlcv != null,
      count: ohlcv?.count ?? 0,
      as_of_date: ohlcv?.as_of_date ?? null,
      // Local file path (present under dual-write); read it for the raw bar series.
      path: ohlcvPath(ticker),
    },
  },
};

console.log(JSON.stringify(payload, null, 2));
process.exit(fresh ? 0 : 3);
