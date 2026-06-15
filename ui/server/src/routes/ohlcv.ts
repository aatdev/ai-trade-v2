import { Router } from 'express';
import { fetchOhlcv } from '../lib/ohlcv';

// Allow an optional EXCHANGE: prefix (e.g. NASDAQ:AAPL) alongside plain tickers.
const SYMBOL_RE = /^[A-Za-z0-9.:\-]{1,20}$/;
const TIMEFRAMES = new Set(['D', 'W', 'M', '240', '120', '60', '30', '15', '5']);
const DEFAULT_COUNT = 300;
const MIN_COUNT = 20;
const MAX_COUNT = 500;

function clampCount(raw: unknown): number {
  const n = Number(raw);
  if (!Number.isFinite(n)) return DEFAULT_COUNT;
  return Math.max(MIN_COUNT, Math.min(MAX_COUNT, Math.floor(n)));
}

/**
 * GET /api/ohlcv/:symbol?tf=D&n=300 — a live, read-only OHLCV series from the
 * vendored `tv` CLI. Always returns 200 with an OhlcvResponse; unreachable
 * TradingView Desktop surfaces as `{ ok:false, error }`.
 */
export function ohlcvRouter(): Router {
  const r = Router();

  r.get('/ohlcv/:symbol', async (req, res) => {
    const symbol = req.params.symbol.toUpperCase();
    if (!SYMBOL_RE.test(symbol)) return res.status(400).json({ error: 'invalid symbol' });

    const tf = String(req.query.tf ?? 'D').toUpperCase();
    if (!TIMEFRAMES.has(tf)) return res.status(400).json({ error: 'invalid timeframe' });

    const count = clampCount(req.query.n);
    const snapshot = await fetchOhlcv(symbol, tf, count);
    return res.json(snapshot);
  });

  return r;
}
