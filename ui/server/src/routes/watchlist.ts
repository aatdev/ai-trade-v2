import fs from 'node:fs';
import path from 'node:path';
import { Router } from 'express';
import { findLatest } from '../lib/files';
import { RE, getWatchlist, readProfile } from '../lib/mappers';
import { applyReconcile, reconcile } from '../lib/reconcile';
import { getAnalysisSignal } from '../lib/signals';
import type { ApplyReconcileResponse } from '@shared/types';

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const TICKER_RE = /^[A-Za-z0-9.\-]{1,10}$/;

const parseDate = (q: unknown): string | null =>
  typeof q === 'string' && DATE_RE.test(q) ? q : null;
const todayISO = (): string => new Date().toISOString().slice(0, 10);

export function watchlistRouter(dataDir: string): Router {
  const r = Router();

  function compute(ticker: string, date: string | null) {
    const wl = getWatchlist(dataDir, date).data;
    const analysis = getAnalysisSignal(dataDir, ticker);
    return { wl, result: reconcile(wl, ticker, analysis, readProfile(dataDir)) };
  }

  // Preview: how would the analysis signal change the watchlist candidate?
  r.get('/watchlist/reconcile/:ticker', (req, res) => {
    const { ticker } = req.params;
    if (!TICKER_RE.test(ticker)) return res.status(400).json({ error: 'invalid ticker' });
    return res.json(compute(ticker, parseDate(req.query.date)).result);
  });

  // Apply: write the merged candidate into the watchlist file (in place).
  r.post('/watchlist/reconcile/:ticker', (req, res) => {
    const { ticker } = req.params;
    if (!TICKER_RE.test(ticker)) return res.status(400).json({ error: 'invalid ticker' });
    const date = parseDate(req.query.date);
    const { wl, result } = compute(ticker, date);

    const applicable =
      result.proposed && result.change !== 'unchanged' && result.change !== 'no-analysis';
    if (!applicable) {
      const body: ApplyReconcileResponse = { result, applied: false, watchlist: wl };
      return res.json(body);
    }

    const targetDate = date ?? result.analysis?.date ?? todayISO();
    const newWl = applyReconcile(wl, result.proposed!, targetDate);

    const scheduleDir = path.join(dataDir, 'schedule');
    let file = findLatest(scheduleDir, RE.watchlist, date);
    if (!file) file = path.join(scheduleDir, `watchlist_${targetDate}.json`);
    try {
      fs.mkdirSync(scheduleDir, { recursive: true });
      fs.writeFileSync(file, `${JSON.stringify(newWl, null, 2)}\n`);
    } catch (e) {
      return res.status(500).json({ error: 'failed to write watchlist', detail: String(e) });
    }

    const body: ApplyReconcileResponse = { result, applied: true, watchlist: newWl };
    return res.json(body);
  });

  return r;
}
