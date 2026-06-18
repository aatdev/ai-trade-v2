/**
 * Fast OHLCV batch fetch: daily bars for one or more symbols in ONE process /
 * ONE CDP connection.
 *
 * Why this exists: driving the chart through separate `tv symbol` + poll +
 * `tv ohlcv` invocations costs ~1s of process/CDP setup PER CALL (3-4 calls
 * per symbol, ~6s/symbol from the Python client). This module reuses the
 * collector's proven in-process pattern (scripts/collect_russell.js): switch
 * the symbol without the embedded 500ms sleep, poll the chart DATA MODEL
 * (not the DOM) every READY_POLL ms until the resolved symbol matches and the
 * bar count is stable, then export bars — ~0.5-1s per symbol all-in.
 */
import { evaluate } from '../connection.js';
import { getOhlcv } from './data.js';

const CHART_API = 'window.TradingViewApi._activeChartWidgetWV.value()';
// Per-symbol ceiling for the readiness poll. A warm chart resolves in
// ~150-450ms; a symbol that never resolves (delisted, bad ticker) gives up
// after this and is reported as a per-symbol failure, not a batch failure.
const READY_TIMEOUT = 8000;
const READY_POLL = 150;
// An invalid/delisted ticker "resolves" with zero bars and would otherwise
// burn the full READY_TIMEOUT. If the resolved symbol matches but the series
// stays empty this long, give up early — a valid symbol on a warm chart shows
// bars well under a second.
const EMPTY_BAIL_MS = 4000;
const MAX_BARS = 500; // mirrors MAX_OHLCV_BARS in core/data.js

const esc = (s) => String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'");

// "NASDAQ:AAPL" -> "AAPL"; bare tickers pass through. Matching on the token
// after the exchange prefix lets callers request either form.
const symbolToken = (s) => {
  const up = String(s || '').toUpperCase();
  const i = up.lastIndexOf(':');
  return i >= 0 ? up.slice(i + 1) : up;
};

// TradingView reports daily as "1D" but accepts "D" in setResolution; treat
// the two spellings (and 1W/W, 1M/M) as the same resolution.
const normTf = (r) => {
  const up = String(r || '').toUpperCase();
  return /^1[DWM]$/.test(up) ? up.slice(1) : up;
};

const isConnectionError = (err) =>
  /CDP|connection|ECONNREFUSED|not running/i.test(err?.message || String(err));

// Fire the symbol switch without core/chart.js setSymbol's embedded 500ms
// sleep — waitForSymbolData() below is the real readiness gate.
async function switchSymbol(symbol) {
  await evaluate(`(function(){ ${CHART_API}.setSymbol('${esc(symbol)}', {}); return true; })()`);
}

async function setResolution(timeframe) {
  await evaluate(`(function(){ ${CHART_API}.setResolution('${esc(timeframe)}', {}); return true; })()`);
}

// Extended hours live on the main-series `sessionId` property ('regular' |
// 'extended'). Reading/setting it lets `tv bars -x` surface pre/post-market
// bars on intraday timeframes regardless of the chart's current setting, then
// restore it so daily-bar consumers (screeners) aren't silently switched.
const SESSION_PROP =
  `${CHART_API}._chartWidget.model().mainSeries().properties().childs().sessionId`;

async function getSessionId() {
  return evaluate(
    `(function(){ try { return ${SESSION_PROP}.value(); } catch(e){ return null; } })()`,
  );
}

async function setSessionId(value) {
  if (!value) return;
  await evaluate(
    `(function(){ try { ${SESSION_PROP}.setValue('${esc(value)}'); return true; } catch(e){ return false; } })()`,
  );
}

/**
 * Poll the chart data model until `symbol`'s bars are loaded on `timeframe`
 * and the bar count is stable across two consecutive polls. Matching the
 * RESOLVED symbol name guards against reading the previous symbol's bars
 * before the switch lands. Returns the last observed state; `.ready` is
 * false on timeout.
 */
async function waitForSymbolData(symbol, timeframe = 'D', timeout = READY_TIMEOUT) {
  const want = symbolToken(symbol);
  const wantTf = normTf(timeframe);
  const start = Date.now();
  let lastSize = -1;
  let stable = 0;
  let last = null;
  while (Date.now() - start < timeout) {
    let st = null;
    try {
      st = await evaluate(`
        (function () {
          try {
            var cw = ${CHART_API};
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
    } catch (err) {
      if (isConnectionError(err)) throw err;
    }
    last = st;
    if (st) {
      const got = symbolToken(st.name || st.chartSymbol);
      const matches = got === want && normTf(st.resolution) === wantTf;
      if (matches && st.size > 0) {
        if (st.size === lastSize) {
          if (++stable >= 1) return { ready: true, ...st };
        } else stable = 0;
        lastSize = st.size;
      } else if (matches && st.size === 0 && Date.now() - start >= EMPTY_BAIL_MS) {
        return { ready: false, empty: true, ...st };
      }
    }
    await new Promise((r) => setTimeout(r, READY_POLL));
  }
  return { ready: false, ...(last || {}) };
}

/**
 * Fetch OHLCV for each symbol sequentially on the shared chart. Per-symbol
 * failures (unresolvable ticker, readiness timeout) are isolated into their
 * result entry; a connection-level failure aborts the whole batch so a dead
 * CDP doesn't burn the connect-retry backoff once per symbol.
 *
 * Returns { success, requested, fetched, failed, results: [
 *   { symbol, success, resolved?, bar_count?, bars?, error?, elapsed_ms } ] }
 * Bars are OLDEST-FIRST {time, open, high, low, close, volume}, exactly as
 * `tv ohlcv` returns them.
 */
export async function getBarsBatch({ symbols, count, timeframe = 'D', extended = false } = {}) {
  if (!symbols || symbols.length === 0) {
    throw new Error('No symbols given. Usage: tv bars <SYMBOL> [SYMBOL...]');
  }
  const limit = Math.min(count || 400, MAX_BARS);
  const results = [];

  // Opt-in extended hours: flip the shared chart to the extended session for
  // the duration of this batch, then restore. A connection error here aborts
  // (same as below) — restore still runs in the finally.
  let prevSession = null;
  if (extended) {
    try {
      prevSession = await getSessionId();
      await setSessionId('extended');
    } catch (err) {
      if (isConnectionError(err)) throw err;
    }
  }

  try {
    for (const symbol of symbols) {
      const t0 = Date.now();
      try {
        await switchSymbol(symbol);
        let st = await waitForSymbolData(symbol, timeframe);
        // First symbol on a chart left on a non-target resolution needs one
        // setResolution; thereafter setSymbol preserves it.
        if (!st.ready && normTf(st.resolution) !== normTf(timeframe)) {
          await setResolution(timeframe);
          st = await waitForSymbolData(symbol, timeframe);
        }
        if (!st.ready) {
          results.push({
            symbol,
            success: false,
            error: st.empty
              ? 'symbol resolved but series is empty (invalid or delisted?)'
              : `timed out waiting for chart data (last: ${st.name || st.chartSymbol || 'n/a'} ${st.resolution || '?'} size=${st.size ?? 0})`,
            elapsed_ms: Date.now() - t0,
          });
          continue;
        }
        const ohlcv = await getOhlcv({ count: limit });
        results.push({
          symbol,
          success: true,
          resolved: st.name || st.chartSymbol,
          bar_count: ohlcv.bar_count,
          bars: ohlcv.bars,
          elapsed_ms: Date.now() - t0,
        });
      } catch (err) {
        if (isConnectionError(err)) throw err;
        results.push({ symbol, success: false, error: err.message, elapsed_ms: Date.now() - t0 });
      }
    }
  } finally {
    // Restore the chart's prior session so daily-bar consumers are unaffected.
    if (extended && prevSession) {
      try {
        await setSessionId(prevSession);
      } catch {
        /* best-effort restore */
      }
    }
  }
  const fetched = results.filter((r) => r.success).length;
  return {
    success: fetched > 0,
    requested: symbols.length,
    fetched,
    failed: symbols.length - fetched,
    results,
  };
}
