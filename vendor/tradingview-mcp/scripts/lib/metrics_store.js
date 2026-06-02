/**
 * Metrics cache store: a per-ticker directory under state/metrics/TICKER/.
 *
 * The cache is the fast path for skills that need indicators / fundamentals /
 * price stats / raw bars — read it instead of driving the live chart. Each file
 * carries `collected_at`; a consumer treats it as stale past STALE_DAYS and
 * falls back to a live TradingView (or other) fetch.
 *
 * Layout (one directory per ticker, BRK.B → BRK_B):
 *   state/metrics/TICKER/metrics.json   — indicators + fundamentals + price summary
 *   state/metrics/TICKER/ohlcv.json     — raw daily bars (OLDEST-FIRST)
 *
 * metrics.json schema:
 *   {
 *     ticker, name, sector, source,
 *     collected_at,            // ISO — when this snapshot was written
 *     as_of_date,              // last daily bar date (yyyy-MM-dd)
 *     bars_count,
 *     quote:   { last, open, high, low, close, volume },
 *     price:   { last_close, year_high, year_low, pct_from_52w_high, avg_volume_50d },
 *     indicators: { ema20/50/200, sma50/150/200, rsi14, macd{}, stoch{}, bb{}, atr14, returns{} },
 *     fundamentals: { ...field groups..., history{} }  // null if unavailable
 *   }
 *
 * ohlcv.json schema:
 *   { ticker, collected_at, as_of_date, count, bars: [{ time, date, open, high, low, close, volume }] }
 *   `time` is UNIX seconds (TradingView native); bars are OLDEST-FIRST.
 */

import { mkdirSync, writeFileSync, readFileSync, existsSync } from 'fs';
import { resolve, join } from 'path';
import { fileURLToPath } from 'url';
import { dirname } from 'path';
import { computeIndicators } from './indicators.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
// scripts/lib → repo root is two levels up.
const REPO_ROOT = resolve(__dirname, '..', '..');
export const METRICS_DIR = join(REPO_ROOT, 'state', 'metrics');

export const STALE_DAYS = 2;
// Cap the locally-cached bar series so --update merges never grow unbounded.
// 1500 daily bars ≈ 6 years — more than any calculator window needs.
export const MAX_OHLCV_BARS = 1500;

/** Filesystem-safe ticker token (BRK.B → BRK_B). Also used as the OpenSearch _id. */
export function safeToken(ticker) {
  return String(ticker).replace(/[^A-Za-z0-9._-]/g, '_');
}

/** Per-ticker cache directory: state/metrics/TICKER/. */
export function tickerDir(ticker) {
  return join(METRICS_DIR, safeToken(ticker));
}

/** Path to the metrics snapshot for a ticker. */
export function metricsPath(ticker) {
  return join(tickerDir(ticker), 'metrics.json');
}

/** Path to the raw OHLCV file for a ticker. */
export function ohlcvPath(ticker) {
  return join(tickerDir(ticker), 'ohlcv.json');
}

/**
 * Assemble the metrics object from a quote + OHLCV bars + (optional) fundamentals.
 * @param {object} a
 * @param {string} a.ticker
 * @param {object} [a.meta]          universe row (Name/Sector)
 * @param {object} [a.quote]         { last, open, high, low, close, volume }
 * @param {Array}  a.bars            OHLCV bars OLDEST-FIRST
 * @param {object} [a.fundamentals]  result of fundamentals.get(), or null
 * @param {string} [a.nowIso]        timestamp override (defaults to wall clock)
 */
export function buildMetrics({
  ticker,
  meta = {},
  quote = null,
  bars = [],
  fundamentals = null,
  nowIso,
}) {
  const indicators = computeIndicators(bars);
  const lastBar = bars.length ? bars[bars.length - 1] : null;
  const asOf = lastBar ? new Date(lastBar.time * 1000).toISOString().slice(0, 10) : null;

  // 52-week price stats from the trailing 252 bars.
  const year = bars.slice(Math.max(0, bars.length - 252));
  const highs = year.map((b) => b.high).filter((v) => v > 0);
  const lows = year.map((b) => b.low).filter((v) => v > 0);
  const last50vol = bars.slice(Math.max(0, bars.length - 50)).map((b) => b.volume ?? 0);
  const lastClose = lastBar ? lastBar.close : null;
  const yearHigh = highs.length ? Math.max(...highs) : null;
  const yearLow = lows.length ? Math.min(...lows) : null;

  return {
    ticker,
    name: meta.Name ?? meta.name ?? ticker,
    sector: meta.Sector ?? meta.sector ?? null,
    source: 'tradingview',
    collected_at: nowIso ?? new Date().toISOString(),
    as_of_date: asOf,
    bars_count: bars.length,
    quote: quote
      ? {
          last: quote.last ?? quote.close ?? lastClose,
          open: quote.open ?? null,
          high: quote.high ?? null,
          low: quote.low ?? null,
          close: quote.close ?? lastClose,
          volume: quote.volume ?? null,
        }
      : null,
    price: {
      last_close: lastClose,
      year_high: yearHigh,
      year_low: yearLow,
      pct_from_52w_high:
        yearHigh && lastClose
          ? Number((((lastClose - yearHigh) / yearHigh) * 100).toFixed(2))
          : null,
      avg_volume_50d: last50vol.length
        ? Math.round(last50vol.reduce((a, b) => a + b, 0) / last50vol.length)
        : null,
    },
    indicators,
    fundamentals: fundamentals && fundamentals.success ? stripFundamentals(fundamentals) : null,
  };
}

/** Drop the bookkeeping keys from a fundamentals.get() result. */
function stripFundamentals(f) {
  const { success, symbol, ...rest } = f;
  return rest;
}

/** Write a metrics object to state/metrics/TICKER/metrics.json (creates the dir). */
export function writeMetrics(metrics) {
  mkdirSync(tickerDir(metrics.ticker), { recursive: true });
  writeFileSync(metricsPath(metrics.ticker), JSON.stringify(metrics, null, 2));
}

/** Read a metrics file, or null if absent/unparseable. */
export function readMetrics(ticker) {
  const p = metricsPath(ticker);
  if (!existsSync(p)) return null;
  try {
    return JSON.parse(readFileSync(p, 'utf-8'));
  } catch {
    return null;
  }
}

// ─── Raw OHLCV ────────────────────────────────────────────────────────────────

/** Normalize a raw bar to the stored shape (UNIX-second `time`, ISO `date`). */
function normalizeBar(b) {
  return {
    time: b.time,
    date: b.date ?? new Date(b.time * 1000).toISOString().slice(0, 10),
    open: b.open,
    high: b.high,
    low: b.low,
    close: b.close,
    volume: b.volume ?? 0,
  };
}

/**
 * Merge two OLDEST-FIRST bar arrays by `time` (incoming wins on overlap), sort
 * ascending, and cap to the most recent `cap` bars. Used so --update (which
 * fetches only missing days) extends the cached series instead of truncating it.
 */
export function mergeBars(existing = [], incoming = [], cap = MAX_OHLCV_BARS) {
  const byTime = new Map();
  for (const b of existing) if (b && b.time != null) byTime.set(b.time, normalizeBar(b));
  for (const b of incoming) if (b && b.time != null) byTime.set(b.time, normalizeBar(b));
  const merged = [...byTime.values()].sort((a, b) => a.time - b.time);
  return merged.length > cap ? merged.slice(merged.length - cap) : merged;
}

/** Write OHLCV bars (OLDEST-FIRST) to state/metrics/TICKER/ohlcv.json. */
export function writeOhlcv(ticker, bars, { nowIso } = {}) {
  mkdirSync(tickerDir(ticker), { recursive: true });
  const last = bars.length ? bars[bars.length - 1] : null;
  const doc = {
    ticker,
    collected_at: nowIso ?? new Date().toISOString(),
    as_of_date: last ? new Date(last.time * 1000).toISOString().slice(0, 10) : null,
    count: bars.length,
    bars,
  };
  writeFileSync(ohlcvPath(ticker), JSON.stringify(doc));
}

/** Read the OHLCV file (full doc), or null if absent/unparseable. */
export function readOhlcv(ticker) {
  const p = ohlcvPath(ticker);
  if (!existsSync(p)) return null;
  try {
    return JSON.parse(readFileSync(p, 'utf-8'));
  } catch {
    return null;
  }
}

/** Read just the OHLCV bar array (OLDEST-FIRST), or [] if absent. */
export function readOhlcvBars(ticker) {
  const doc = readOhlcv(ticker);
  return doc?.bars ?? [];
}

/** Age of a metrics snapshot in days, or Infinity if missing/invalid. */
export function metricsAgeDays(metrics, nowMs = Date.now()) {
  if (!metrics?.collected_at) return Infinity;
  const t = Date.parse(metrics.collected_at);
  if (Number.isNaN(t)) return Infinity;
  return (nowMs - t) / 86400000;
}

/** True when the snapshot exists and is younger than STALE_DAYS. */
export function isFresh(metrics, nowMs = Date.now()) {
  return metricsAgeDays(metrics, nowMs) <= STALE_DAYS;
}
