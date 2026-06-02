#!/usr/bin/env node
/**
 * Daily metrics-cache collector (dual-written: local files + OpenSearch).
 *
 * Reads a ticker universe (Russell 2000 by default, or S&P 500), fetches daily
 * bars per ticker from a live TradingView Desktop chart (CDP on :9222), and
 * writes the per-ticker cache under state/metrics/TICKER/:
 *   metrics.json — locally-computed indicators + TradingView fundamentals + price summary
 *   ohlcv.json   — raw daily bars (OLDEST-FIRST)
 * The same snapshot is mirrored to OpenSearch (indices my_tw_metrics +
 * my_tw_candles_1d) — see scripts/lib/opensearch.js. The OpenSearch push is
 * best-effort: if the server is unreachable the run continues with files only.
 * See scripts/lib/metrics_store.js for the schema and staleness rules.
 *
 * Resume/update state is derived from the cache itself (metrics.json's
 * `collected_at` / `as_of_date`) — there is no separate state store. Disable the
 * OpenSearch path with METRICS_OPENSEARCH=0; override the URL with OPENSEARCH_URL.
 *
 * Universes (--source):
 *   russell  → state/russel2000.json  (default; JSON array, iShares export shape)
 *   snp500   → state/sp500.csv        (Wikipedia constituents CSV)
 *
 * Usage:
 *   node scripts/collect_russell.js                   # collect Russell 2000, skip already done
 *   node scripts/collect_russell.js --source snp500   # collect S&P 500 instead
 *   node scripts/collect_russell.js --update          # refresh: fetch only missing days, merge
 *   node scripts/collect_russell.js --from CRDO       # resume from a specific ticker
 *   node scripts/collect_russell.js --limit 50        # process only first N tickers
 *   node scripts/collect_russell.js --ticker AAPL     # single ticker
 *   node scripts/collect_russell.js --no-fundamentals # skip the fundamentals fetch
 */

import { readFileSync } from 'fs';
import { resolve } from 'path';
import { setSymbol, setTimeframe } from '../src/core/chart.js';
import { getOhlcv, getQuote } from '../src/core/data.js';
import { get as getFundamentals } from '../src/core/fundamentals.js';
import { disconnect, evaluate } from '../src/connection.js';
import {
  buildMetrics,
  writeMetrics,
  writeOhlcv,
  mergeBars,
  readOhlcvBars,
  readMetrics,
} from './lib/metrics_store.js';
import * as os from './lib/opensearch.js';

// ─── Config ──────────────────────────────────────────────────────────────────

const BARS_FULL = 1000;
const UPDATE_OVERLAP = 21; // safety overlap added to missing-days count
const UPDATE_MIN = 10; // never fetch fewer than this in update mode
// Adaptive readiness (replaces the old fixed 2500ms/1000ms sleeps): poll the data
// model directly until the requested symbol's daily bars have loaded and the bar
// count is stable. Resolves in ~150–600ms typically instead of a flat 2.5s, and
// guards against reading the PREVIOUS symbol's bars (the count-stability + symbol
// match is a correctness check the fixed sleep never had).
const READY_TIMEOUT = 12000; // ms hard cap waiting for a symbol to load
const READY_POLL = 150; // ms between readiness polls
const SLEEP_BETWEEN = 0; // adaptive wait already paces the loop; no fixed gap needed

// ─── Args ─────────────────────────────────────────────────────────────────────

function parseArgs() {
  const args = process.argv.slice(2);
  const opts = {
    update: false,
    from: null,
    limit: null,
    ticker: null,
    source: 'russell',
    fundamentals: true,
  };
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--update') opts.update = true;
    else if (args[i] === '--from') opts.from = args[++i];
    else if (args[i] === '--limit') opts.limit = parseInt(args[++i]);
    else if (args[i] === '--ticker') opts.ticker = args[++i];
    else if (args[i] === '--source') opts.source = args[++i];
    else if (args[i] === '--no-fundamentals') opts.fundamentals = false;
  }
  return opts;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function todayDate() {
  return new Date().toISOString().slice(0, 10);
}

function daysBetween(fromDateStr, toDateStr) {
  const from = new Date(fromDateStr + 'T00:00:00Z');
  const to = new Date(toDateStr + 'T00:00:00Z');
  return Math.round((to - from) / 86400000);
}

// ─── Universe loading ─────────────────────────────────────────────────────────

// Source name → file + parser. Both parsers return rows in the iShares-export
// shape used downstream (meta.Ticker, meta.Name, meta.Sector, …).
const SOURCES = {
  russell: { file: 'state/russel2000.json', parse: parseRussellJson },
  snp500: { file: 'state/sp500.csv', parse: parseSnp500Csv },
};
SOURCES.sp500 = SOURCES.snp500; // alias

function parseRussellJson(text) {
  return JSON.parse(text);
}

// Minimal RFC-4180 CSV parser (handles quoted fields with embedded commas).
function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = '';
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else inQuotes = false;
      } else field += c;
    } else if (c === '"') inQuotes = true;
    else if (c === ',') {
      row.push(field);
      field = '';
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      row.push(field);
      field = '';
      if (row.length > 1 || row[0] !== '') rows.push(row);
      row = [];
    } else field += c;
  }
  if (field !== '' || row.length) {
    row.push(field);
    if (row.length > 1 || row[0] !== '') rows.push(row);
  }
  return rows;
}

// Wikipedia S&P 500 constituents CSV → iShares-export shape.
function parseSnp500Csv(text) {
  const rows = parseCsv(text);
  const header = rows.shift().map((h) => h.trim());
  const col = (name) => header.indexOf(name);
  const iSym = col('Symbol');
  const iName = col('Security');
  const iSector = col('GICS Sector');
  const iLoc = col('Headquarters Location');
  return rows
    .filter((r) => r[iSym]?.trim())
    .map((r) => ({
      // Wikipedia uses dotted class tickers (BRK.B); TradingView accepts them as-is.
      Ticker: r[iSym].trim(),
      Name: (r[iName] ?? '').trim(),
      Sector: (r[iSector] ?? '').trim(),
      'Asset Class': 'Equity',
      Location: (r[iLoc] ?? '').trim(),
      Exchange: '',
      Currency: 'USD',
      'Weight (%)': '',
      'Market Value': '',
      Price: '',
    }));
}

function loadUniverse(source) {
  const def = SOURCES[source];
  if (!def) {
    console.error(`Unknown --source "${source}". Available: ${Object.keys(SOURCES).join(', ')}`);
    process.exit(1);
  }
  const path = resolve(def.file);
  return def.parse(readFileSync(path, 'utf-8'));
}

// ─── TradingView data ─────────────────────────────────────────────────────────

// Poll the chart data model until `symbol`'s daily bars are loaded and stable.
// Returns the last observed state; `.ready` is false on timeout (caller proceeds
// best-effort, same as the old fixed-sleep path). `symbolInfoName` is the exact
// resolved ticker (e.g. "MSFT") — matching it guards against reading the previous
// symbol's bars before the switch has landed.
async function waitForSymbolData(symbol) {
  const want = symbol.toUpperCase();
  const start = Date.now();
  let lastSize = -1;
  let stable = 0;
  let last = null;
  while (Date.now() - start < READY_TIMEOUT) {
    const st = await evaluate(`
      (function () {
        try {
          var cw = window.TradingViewApi._activeChartWidgetWV.value();
          var ms = cw._chartWidget.model().mainSeries();
          var si = null; try { si = ms.symbolInfo(); } catch (e) {}
          var bars = ms.bars();
          return {
            chartSymbol: cw.symbol(),
            name: si ? (si.name || si.full_name || si.pro_name || '') : '',
            resolution: cw.resolution(),
            size: bars && bars.size ? bars.size() : 0,
          };
        } catch (e) { return null; }
      })()
    `);
    last = st;
    if (st && st.size > 0) {
      const sym = (st.name || st.chartSymbol || '').toUpperCase();
      const matches = sym === want || sym.endsWith(':' + want) || sym.endsWith(' ' + want);
      if (matches && st.resolution === '1D') {
        if (st.size === lastSize) {
          if (++stable >= 1) return { ready: true, ...st };
        } else stable = 0;
        lastSize = st.size;
      }
    }
    await sleep(READY_POLL);
  }
  return { ready: false, ...(last || {}) };
}

async function fetchBars(symbol, count) {
  await setSymbol({ symbol, wait: false }); // skip the ~10s DOM wait; we poll the model below
  let st = await waitForSymbolData(symbol);

  // First ticker (or any symbol that loaded on a non-daily resolution) needs the
  // timeframe set to D once; thereafter setSymbol preserves the resolution, so we
  // skip the redundant per-ticker setTimeframe + its old 1000ms sleep.
  if (st.resolution !== '1D') {
    await setTimeframe({ timeframe: 'D' });
    st = await waitForSymbolData(symbol);
  }

  const [quote, ohlcv] = await Promise.all([getQuote({}), getOhlcv({ count })]);

  return { quote, bars: ohlcv.bars ?? [] };
}

// Fetch fundamentals for the symbol currently on the chart. The scanner endpoint
// needs EXCHANGE:TICKER; reading the active chart (no symbol arg) resolves it.
// Returns null on any failure — fundamentals are best-effort in the snapshot.
async function fetchFundamentalsSafe() {
  try {
    return await getFundamentals({ history: true });
  } catch {
    return null;
  }
}

// ─── Main loop ────────────────────────────────────────────────────────────────

async function processTicker(ticker, meta, opts) {
  const today = todayDate();
  const existing = readMetrics(ticker); // local cache is the source of truth

  // Decide how many bars to fetch
  let barsToFetch = BARS_FULL;
  let isUpdate = false;

  if (existing) {
    if (!opts.update) {
      process.stdout.write('skip\n');
      return 'skipped';
    }

    // Already refreshed today — nothing to do
    if (existing.collected_at?.slice(0, 10) === today) {
      process.stdout.write('skip (today)\n');
      return 'skipped';
    }

    // Fetch only missing days (+ overlap); fall back to full if gap is too large
    const missing = existing.as_of_date ? daysBetween(existing.as_of_date, today) : null;
    if (missing != null && missing > 0 && missing + UPDATE_OVERLAP < BARS_FULL) {
      barsToFetch = Math.max(missing + UPDATE_OVERLAP, UPDATE_MIN);
    }
    isUpdate = true;
  }

  // Fetch from TradingView (chart lands on `ticker`, so fundamentals resolve it).
  const { quote, bars } = await fetchBars(ticker, barsToFetch);
  if (!bars.length) throw new Error('No bars returned');

  // Merge the freshly-fetched bars into the stored series so --update (which
  // pulls only missing days) extends rather than truncates it, then compute
  // indicators from the FULL series — otherwise EMA200 etc. would be null on an
  // incremental refresh.
  const nowIso = new Date().toISOString();
  const fullBars = mergeBars(readOhlcvBars(ticker), bars);
  writeOhlcv(ticker, fullBars, { nowIso });

  const fundamentals = opts.fundamentals ? await fetchFundamentalsSafe() : null;
  const metrics = buildMetrics({ ticker, meta, quote, bars: fullBars, fundamentals, nowIso });
  writeMetrics(metrics);

  // Mirror to OpenSearch (best-effort; falls through to files-only if unreachable).
  await os.writeMetrics(metrics);
  await os.writeCandles(ticker, fullBars, nowIso);

  const lastBar = fullBars[fullBars.length - 1];
  const tag = isUpdate ? 'upd' : 'new';
  process.stdout.write(
    `✓  [${tag}] bars=${fullBars.length} (+${bars.length} fetched)  last=${lastBar.date}  ` +
      `price=${quote.last ?? quote.close}${fundamentals ? '  +f' : ''}${os.osActive() ? '  +os' : ''}\n`
  );
  return 'done';
}

async function main() {
  const opts = parseArgs();

  // Load tickers
  const allTickers = loadUniverse(opts.source);
  console.log(`Loaded ${allTickers.length} tickers from ${SOURCES[opts.source].file}`);

  // Apply --ticker filter
  let tickers = opts.ticker ? allTickers.filter((t) => t.Ticker === opts.ticker) : allTickers;

  // Apply --from filter (resume)
  if (opts.from) {
    const idx = tickers.findIndex((t) => t.Ticker === opts.from);
    if (idx === -1) {
      console.error(`Ticker ${opts.from} not found`);
      process.exit(1);
    }
    tickers = tickers.slice(idx);
    console.log(`Resuming from ${opts.from} (${tickers.length} tickers remaining)`);
  }

  // Apply --limit
  if (opts.limit) tickers = tickers.slice(0, opts.limit);

  const mode = opts.update ? 'UPDATE' : 'COLLECT';
  console.log(
    `\nMode: ${mode} | Tickers: ${tickers.length} | ` +
      `Bars: ${opts.update ? 'auto (missing days + ' + UPDATE_OVERLAP + ' overlap)' : BARS_FULL} | ` +
      `Fundamentals: ${opts.fundamentals ? 'on' : 'off'} | ` +
      `OpenSearch: ${os.OS_ENABLED ? os.OS_BASE : 'off'}\n`
  );

  // Create the OpenSearch indices up front (no-op if they exist or the server is down).
  await os.ensureIndices();

  const stats = { done: 0, updated: 0, skipped: 0, failed: 0 };

  for (let i = 0; i < tickers.length; i++) {
    const meta = tickers[i];
    const sym = meta.Ticker;
    process.stdout.write(`[${String(i + 1).padStart(4)}/${tickers.length}] ${sym.padEnd(8)} `);

    try {
      const result = await processTicker(sym, meta, opts);
      if (result === 'skipped') stats.skipped++;
      else if (opts.update) stats.updated++;
      else stats.done++;
    } catch (err) {
      process.stdout.write(`✗  ${err.message}\n`);
      stats.failed++;
    }

    if (SLEEP_BETWEEN && i < tickers.length - 1) await sleep(SLEEP_BETWEEN);
  }

  console.log(`\n${'━'.repeat(50)}`);
  console.log(
    `Collected: ${stats.done}  Updated: ${stats.updated}  Skipped: ${stats.skipped}  Failed: ${stats.failed}`
  );

  await disconnect();
}

main().catch((err) => {
  console.error('\nFatal:', err.message);
  process.exit(1);
});
