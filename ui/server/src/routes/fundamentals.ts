import { Router } from 'express';
import { fetchFundamentals } from '../lib/fundamentals';

// Allow an optional EXCHANGE: prefix (e.g. NASDAQ:AAPL) alongside plain tickers.
const SYMBOL_RE = /^[A-Za-z0-9.:\-]{1,20}$/;

/**
 * GET /api/fundamentals/:symbol — company profile + key metrics from the public
 * TradingView scanner. Always returns 200 with a FundamentalsResponse; an
 * unreachable scanner (or an unqualified symbol) surfaces as `{ ok:false, error }`.
 */
export function fundamentalsRouter(): Router {
  const r = Router();

  r.get('/fundamentals/:symbol', async (req, res) => {
    const symbol = req.params.symbol.toUpperCase();
    if (!SYMBOL_RE.test(symbol)) return res.status(400).json({ error: 'invalid symbol' });

    const snapshot = await fetchFundamentals(symbol);
    return res.json(snapshot);
  });

  return r;
}
