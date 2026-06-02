/**
 * Pure technical-indicator math, computed locally from OHLCV bars.
 *
 * Why local: the metrics collector walks ~2000 Russell tickers; navigating the
 * chart to attach EMA/RSI/MACD/etc. per ticker and reading study values would be
 * slow and fragile. Every indicator here is a deterministic function of the
 * daily bars the collector already pulls, so we derive them in-process.
 *
 * Convention: every helper takes arrays OLDEST-FIRST (the shape getOhlcv returns)
 * and yields the LATEST value, or null when there is not enough history.
 */

// ─── Moving averages ────────────────────────────────────────────────────────

/** Simple moving average of the last `period` values. */
export function sma(values, period) {
  if (!values || values.length < period) return null;
  let sum = 0;
  for (let i = values.length - period; i < values.length; i++) sum += values[i];
  return sum / period;
}

/** Full EMA series (oldest-first), seeded with the SMA of the first `period`. */
function emaSeries(values, period) {
  if (!values || values.length < period) return null;
  const k = 2 / (period + 1);
  let prev = 0;
  for (let i = 0; i < period; i++) prev += values[i];
  prev /= period;
  const out = new Array(period - 1).fill(null);
  out.push(prev);
  for (let i = period; i < values.length; i++) {
    prev = values[i] * k + prev * (1 - k);
    out.push(prev);
  }
  return out;
}

/** Latest EMA value. */
export function ema(values, period) {
  const s = emaSeries(values, period);
  return s ? s[s.length - 1] : null;
}

// ─── RSI (Wilder) ─────────────────────────────────────────────────────────────

export function rsi(closes, period = 14) {
  if (!closes || closes.length < period + 1) return null;
  let gain = 0;
  let loss = 0;
  for (let i = 1; i <= period; i++) {
    const d = closes[i] - closes[i - 1];
    if (d >= 0) gain += d;
    else loss -= d;
  }
  let avgGain = gain / period;
  let avgLoss = loss / period;
  for (let i = period + 1; i < closes.length; i++) {
    const d = closes[i] - closes[i - 1];
    avgGain = (avgGain * (period - 1) + (d > 0 ? d : 0)) / period;
    avgLoss = (avgLoss * (period - 1) + (d < 0 ? -d : 0)) / period;
  }
  if (avgLoss === 0) return 100;
  const rs = avgGain / avgLoss;
  return 100 - 100 / (1 + rs);
}

// ─── MACD (12, 26, 9) ───────────────────────────────────────────────────────

export function macd(closes, fast = 12, slow = 26, signalPeriod = 9) {
  if (!closes || closes.length < slow + signalPeriod) return null;
  const fastS = emaSeries(closes, fast);
  const slowS = emaSeries(closes, slow);
  if (!fastS || !slowS) return null;

  // MACD line where both EMAs are defined (oldest-first).
  const macdLine = [];
  for (let i = 0; i < closes.length; i++) {
    if (fastS[i] == null || slowS[i] == null) continue;
    macdLine.push(fastS[i] - slowS[i]);
  }
  const signalS = emaSeries(macdLine, signalPeriod);
  if (!signalS) return null;
  const line = macdLine[macdLine.length - 1];
  const signal = signalS[signalS.length - 1];
  return { line, signal, hist: line - signal };
}

// ─── Stochastic (14, 3, 3) — slow %K / %D ─────────────────────────────────────

export function stochastic(highs, lows, closes, kPeriod = 14, kSmooth = 3, dSmooth = 3) {
  const n = closes?.length ?? 0;
  if (n < kPeriod + kSmooth + dSmooth) return null;

  const rawK = [];
  for (let i = kPeriod - 1; i < n; i++) {
    let hh = -Infinity;
    let ll = Infinity;
    for (let j = i - kPeriod + 1; j <= i; j++) {
      if (highs[j] > hh) hh = highs[j];
      if (lows[j] < ll) ll = lows[j];
    }
    rawK.push(hh === ll ? 50 : (100 * (closes[i] - ll)) / (hh - ll));
  }
  // Slow %K = SMA(rawK, kSmooth); %D = SMA(slowK, dSmooth).
  const slowK = smaSeries(rawK, kSmooth);
  const dSeries = smaSeries(slowK, dSmooth);
  if (!slowK.length || !dSeries.length) return null;
  return { k: slowK[slowK.length - 1], d: dSeries[dSeries.length - 1] };
}

/** Trailing SMA series over `period` (oldest-first, only defined points). */
function smaSeries(values, period) {
  const out = [];
  for (let i = period - 1; i < values.length; i++) {
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += values[j];
    out.push(sum / period);
  }
  return out;
}

// ─── Bollinger Bands (20, 2) ──────────────────────────────────────────────────

export function bollinger(closes, period = 20, mult = 2) {
  if (!closes || closes.length < period) return null;
  const window = closes.slice(closes.length - period);
  const mid = window.reduce((a, b) => a + b, 0) / period;
  const variance = window.reduce((a, b) => a + (b - mid) ** 2, 0) / period;
  const sd = Math.sqrt(variance);
  const upper = mid + mult * sd;
  const lower = mid - mult * sd;
  return { upper, middle: mid, lower, width: mid ? (upper - lower) / mid : null };
}

// ─── ATR (Wilder, 14) ─────────────────────────────────────────────────────────

export function atr(highs, lows, closes, period = 14) {
  const n = closes?.length ?? 0;
  if (n < period + 1) return null;
  const tr = [];
  for (let i = 1; i < n; i++) {
    tr.push(
      Math.max(
        highs[i] - lows[i],
        Math.abs(highs[i] - closes[i - 1]),
        Math.abs(lows[i] - closes[i - 1])
      )
    );
  }
  let prev = 0;
  for (let i = 0; i < period; i++) prev += tr[i];
  prev /= period;
  for (let i = period; i < tr.length; i++) prev = (prev * (period - 1) + tr[i]) / period;
  return prev;
}

// ─── Trailing returns ─────────────────────────────────────────────────────────

/** % price change over `lookback` bars (close-to-close), or null. */
export function pctReturn(closes, lookback) {
  if (!closes || closes.length <= lookback) return null;
  const now = closes[closes.length - 1];
  const then = closes[closes.length - 1 - lookback];
  if (!then) return null;
  return ((now - then) / then) * 100;
}

// ─── Convenience: compute the full indicator block from bars ──────────────────

/**
 * Build the indicator block from OHLCV bars (oldest-first).
 * Each bar: {open, high, low, close, volume}. Missing-history fields are null.
 */
export function computeIndicators(bars) {
  if (!Array.isArray(bars) || !bars.length) return null;
  const closes = bars.map((b) => b.close);
  const highs = bars.map((b) => b.high);
  const lows = bars.map((b) => b.low);
  const round = (v, d = 4) => (v == null ? null : Number(v.toFixed(d)));

  const bb = bollinger(closes);
  const mac = macd(closes);
  const st = stochastic(highs, lows, closes);

  return {
    ema20: round(ema(closes, 20)),
    ema50: round(ema(closes, 50)),
    ema200: round(ema(closes, 200)),
    sma50: round(sma(closes, 50)),
    sma150: round(sma(closes, 150)),
    sma200: round(sma(closes, 200)),
    rsi14: round(rsi(closes, 14), 2),
    macd: mac ? { line: round(mac.line), signal: round(mac.signal), hist: round(mac.hist) } : null,
    stoch: st ? { k: round(st.k, 2), d: round(st.d, 2) } : null,
    bb: bb
      ? {
          upper: round(bb.upper),
          middle: round(bb.middle),
          lower: round(bb.lower),
          width: round(bb.width, 4),
        }
      : null,
    atr14: round(atr(highs, lows, closes, 14)),
    returns: {
      // ~21 trading days/month, 63/quarter, 126/half, 252/year.
      r_1m: round(pctReturn(closes, 21), 2),
      r_3m: round(pctReturn(closes, 63), 2),
      r_6m: round(pctReturn(closes, 126), 2),
      r_12m: round(pctReturn(closes, 252), 2),
    },
  };
}
