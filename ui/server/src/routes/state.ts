import fs from 'node:fs';
import path from 'node:path';
import { Router } from 'express';
import { basename, findLatest, listDates, listDir, readJson, readText, tailLines } from '../lib/files';
import { deleteSignal, parseSignalBlocks, signalsFile } from '../lib/signals';
import {
  RE,
  getExposureGate,
  getMemory,
  getPortfolio,
  getPosture,
  getRegime,
  getScreener,
  getTheses,
  getThesisDetail,
  getWatchlist,
} from '../lib/mappers';
import type {
  AutopilotResponse,
  AutopilotState,
  DatesResponse,
  ExposureResponse,
  MarketResponse,
  ScreenersResponse,
  Sourced,
  ThesesResponse,
  TradingProfile,
} from '@shared/types';

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const THESIS_ID_RE = /^[a-z0-9_]+$/i;
const TICKER_RE = /^[A-Za-z0-9.\-]{1,10}$/;

function parseDate(q: unknown): string | null {
  return typeof q === 'string' && DATE_RE.test(q) ? q : null;
}

export function stateRouter(dataDir: string): Router {
  const r = Router();

  r.get('/dates', (_req, res) => {
    const dirs = ['schedule', 'market', 'screeners', 'journal'].map((d) => path.join(dataDir, d));
    const dates = listDates(dirs);
    const body: DatesResponse = { dates, latest: dates[0] ?? null };
    res.json(body);
  });

  r.get('/exposure', (req, res) => {
    const date = parseDate(req.query.date);
    const body: ExposureResponse = {
      gate: getExposureGate(dataDir, date),
      posture: getPosture(dataDir, date),
    };
    res.json(body);
  });

  r.get('/watchlist', (req, res) => {
    res.json(getWatchlist(dataDir, parseDate(req.query.date)));
  });

  r.get('/portfolio', (req, res) => {
    res.json(getPortfolio(dataDir, parseDate(req.query.date)));
  });

  r.get('/market', (req, res) => {
    const date = parseDate(req.query.date);
    const body: MarketResponse = {
      breadth: getRegime(dataDir, RE.breadth, date),
      uptrend: getRegime(dataDir, RE.uptrend, date),
      top: getRegime(dataDir, RE.top, date),
      macro: getRegime(dataDir, RE.macro, date),
      posture: getPosture(dataDir, date),
    };
    res.json(body);
  });

  r.get('/screeners', (req, res) => {
    const date = parseDate(req.query.date);
    const body: ScreenersResponse = {
      vcp: getScreener(dataDir, 'vcp', date),
      swingShort: getScreener(dataDir, 'swing-short', date),
    };
    res.json(body);
  });

  r.get('/theses', (_req, res) => {
    const body: ThesesResponse = { theses: getTheses(dataDir) };
    res.json(body);
  });

  r.get('/theses/:id', (req, res) => {
    const id = req.params.id;
    if (!THESIS_ID_RE.test(id)) return res.status(400).json({ error: 'invalid thesis id' });
    const detail = getThesisDetail(dataDir, id);
    if (!detail) return res.status(404).json({ error: 'thesis not found' });
    return res.json(detail);
  });

  r.get('/memory', (_req, res) => {
    res.json(getMemory(dataDir));
  });

  r.get('/signals', (_req, res) => {
    const text = readText(signalsFile(dataDir));
    res.json({
      content: text ?? '',
      present: text != null,
      signals: text ? parseSignalBlocks(text) : [],
    });
  });

  r.delete('/signals/:ticker/:date', (req, res) => {
    const { ticker, date } = req.params;
    if (!TICKER_RE.test(ticker)) return res.status(400).json({ error: 'invalid ticker' });
    if (!DATE_RE.test(date)) return res.status(400).json({ error: 'invalid date' });
    const result = deleteSignal(dataDir, ticker, date);
    if (!result.found) return res.status(404).json({ error: 'signal not found', ...result });
    return res.json(result);
  });

  r.get('/analysis/tickers', (_req, res) => {
    const dir = path.join(dataDir, 'analysis');
    const tickers: Record<string, { latest: string | null; count: number; dates: string[] }> = {};
    for (const name of listDir(dir)) {
      if (!TICKER_RE.test(name)) continue;
      const sub = path.join(dir, name);
      let dates: string[] = [];
      try {
        if (!fs.statSync(sub).isDirectory()) continue;
        dates = listDir(sub).filter((d) => DATE_RE.test(d));
      } catch {
        continue;
      }
      if (dates.length > 0) {
        dates.sort();
        tickers[name.toUpperCase()] = {
          latest: dates[dates.length - 1],
          count: dates.length,
          dates,
        };
      }
    }
    res.json({ tickers });
  });

  r.get('/profile', (_req, res) => {
    const profile = readJson<TradingProfile>(path.join(dataDir, '..', 'trading_profile.json'));
    // trading_profile.json lives at the repo root, i.e. one level above trading-data/
    res.json(profile ?? readJson<TradingProfile>(path.join(dataDir, 'trading_profile.json')) ?? null);
  });

  r.get('/autopilot', (req, res) => {
    const date = parseDate(req.query.date);
    const state = readJson<AutopilotState>(path.join(dataDir, 'logs', 'autopilot_state.json'));
    const weeklyFile = findLatest(path.join(dataDir, 'schedule'), RE.weeklyReview, date);
    const monthlyFile = findLatest(path.join(dataDir, 'schedule'), RE.monthlyReview, date);
    const weeklyReview: Sourced<Record<string, unknown>> = {
      date,
      source: basename(weeklyFile),
      data: readJson(weeklyFile),
    };
    const monthlyReview: Sourced<Record<string, unknown>> = {
      date,
      source: basename(monthlyFile),
      data: readJson(monthlyFile),
    };
    const body: AutopilotResponse = {
      state: state ?? null,
      weeklyReview,
      monthlyReview,
      logTail: tailLines(path.join(dataDir, 'logs', 'trading_schedule.log'), 200),
    };
    res.json(body);
  });

  return r;
}
