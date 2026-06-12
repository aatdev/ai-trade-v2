import fs from 'node:fs';
import path from 'node:path';
import { Router } from 'express';
import { listDir, readText } from '../lib/files';
import type { TickerAnalysisResponse, TickerDatesResponse, TickerDoc } from '@shared/types';

const SYMBOL_RE = /^[A-Za-z0-9.\-]{1,12}$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const TF = new Set(['daily', 'weekly']);
const DOC_NAMES = ['report', 'technical', 'fundamental', 'news'];

export function tickerRouter(dataDir: string): Router {
  const r = Router();
  const analysisDir = path.join(dataDir, 'analysis');

  r.get('/ticker/:symbol', (req, res) => {
    const symbol = req.params.symbol;
    if (!SYMBOL_RE.test(symbol)) return res.status(400).json({ error: 'invalid symbol' });
    const dir = path.join(analysisDir, symbol);
    const dates = listDir(dir)
      .filter((n) => DATE_RE.test(n) && fs.existsSync(path.join(dir, n)) && fs.statSync(path.join(dir, n)).isDirectory())
      .sort()
      .reverse();
    const body: TickerDatesResponse = { symbol, dates };
    return res.json(body);
  });

  r.get('/ticker/:symbol/:date', (req, res) => {
    const { symbol, date } = req.params;
    if (!SYMBOL_RE.test(symbol)) return res.status(400).json({ error: 'invalid symbol' });
    if (!DATE_RE.test(date)) return res.status(400).json({ error: 'invalid date' });
    const dir = path.join(analysisDir, symbol, date);
    const docs: TickerDoc[] = [];
    for (const name of DOC_NAMES) {
      const content = readText(path.join(dir, `${name}.md`));
      if (content != null) docs.push({ name, content });
    }
    const charts = listDir(dir)
      .map((n) => n.match(/_(daily|weekly)\.png$/i)?.[1]?.toLowerCase())
      .filter((tf): tf is string => !!tf);
    const body: TickerAnalysisResponse = { symbol, date, docs, charts: Array.from(new Set(charts)) };
    return res.json(body);
  });

  r.get('/ticker/:symbol/:date/chart/:tf', (req, res) => {
    const { symbol, date, tf } = req.params;
    if (!SYMBOL_RE.test(symbol)) return res.status(400).json({ error: 'invalid symbol' });
    if (!DATE_RE.test(date)) return res.status(400).json({ error: 'invalid date' });
    if (!TF.has(tf)) return res.status(400).json({ error: 'invalid timeframe' });
    const dir = path.join(analysisDir, symbol, date);
    const match = listDir(dir).find((n) => new RegExp(`_${tf}\\.png$`, 'i').test(n));
    if (!match) return res.status(404).json({ error: 'chart not found' });
    return res.sendFile(path.join(dir, match));
  });

  return r;
}
