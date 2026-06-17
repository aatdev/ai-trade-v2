import fs from 'node:fs';
import path from 'node:path';
import { Router } from 'express';
import {
  basename,
  findLatest,
  listDates,
  listDir,
  listLatest,
  readJson,
  readText,
  tailLines,
} from '../lib/files';
import { deleteSignal, parseSignalBlocks, signalsFile } from '../lib/signals';
import {
  buildSaveResponse,
  readProfileFile,
  validateProfilePatch,
  writeProfileFile,
} from '../lib/profile';
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
  SaveProfileResponse,
  ScreenersResponse,
  Sourced,
  ThesesResponse,
  VersionsResponse,
} from '@shared/types';

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const THESIS_ID_RE = /^[a-z0-9_]+$/i;
const TICKER_RE = /^[A-Za-z0-9.\-]{1,10}$/;
const SOURCE_RE = /^[A-Za-z0-9_.\-]{1,80}\.json$/;

function parseDate(q: unknown): string | null {
  return typeof q === 'string' && DATE_RE.test(q) ? q : null;
}

/** Sanity gate for a `?source=` basename; the mapper does the strict pattern + membership check. */
function parseSource(q: unknown): string | null {
  return typeof q === 'string' && SOURCE_RE.test(q) ? q : null;
}

/** Selectable file kinds for GET /api/versions → (subdir, filename pattern). */
const VERSION_KINDS: Record<string, { dir: string; re: RegExp }> = {
  exposure: { dir: 'schedule', re: RE.exposureDecision },
  watchlist: { dir: 'schedule', re: RE.watchlist },
  portfolio: { dir: 'journal', re: RE.portfolioHeat },
  vcp: { dir: 'screeners', re: RE.vcp },
  'swing-short': { dir: 'screeners', re: RE.swingShort },
  breadth: { dir: 'market', re: RE.breadth },
  uptrend: { dir: 'market', re: RE.uptrend },
  top: { dir: 'market', re: RE.top },
  macro: { dir: 'market', re: RE.macro },
};

export function stateRouter(dataDir: string): Router {
  const r = Router();

  r.get('/dates', (_req, res) => {
    const dirs = ['schedule', 'market', 'screeners', 'journal'].map((d) => path.join(dataDir, d));
    const dates = listDates(dirs);
    const body: DatesResponse = { dates, latest: dates[0] ?? null };
    res.json(body);
  });

  // Last 10 file versions for a selectable kind (newest first), so the UI can
  // pin a specific historical snapshot via the matching `?source=` param below.
  r.get('/versions', (req, res) => {
    const kind = typeof req.query.kind === 'string' ? req.query.kind : '';
    const spec = VERSION_KINDS[kind];
    if (!spec) return res.status(400).json({ error: 'invalid kind' });
    const versions = listLatest(path.join(dataDir, spec.dir), spec.re, 10);
    const body: VersionsResponse = { kind, versions };
    return res.json(body);
  });

  r.get('/exposure', (req, res) => {
    const date = parseDate(req.query.date);
    const body: ExposureResponse = {
      gate: getExposureGate(dataDir, date, parseSource(req.query.source)),
      posture: getPosture(dataDir, date),
    };
    res.json(body);
  });

  r.get('/watchlist', (req, res) => {
    res.json(getWatchlist(dataDir, parseDate(req.query.date), parseSource(req.query.source)));
  });

  r.get('/portfolio', (req, res) => {
    res.json(getPortfolio(dataDir, parseDate(req.query.date), parseSource(req.query.source)));
  });

  r.get('/market', (req, res) => {
    const date = parseDate(req.query.date);
    const body: MarketResponse = {
      breadth: getRegime(dataDir, RE.breadth, date, parseSource(req.query.breadthSource)),
      uptrend: getRegime(dataDir, RE.uptrend, date, parseSource(req.query.uptrendSource)),
      top: getRegime(dataDir, RE.top, date, parseSource(req.query.topSource)),
      macro: getRegime(dataDir, RE.macro, date, parseSource(req.query.macroSource)),
      posture: getPosture(dataDir, date),
    };
    res.json(body);
  });

  r.get('/screeners', (req, res) => {
    const date = parseDate(req.query.date);
    const body: ScreenersResponse = {
      vcp: getScreener(dataDir, 'vcp', date, parseSource(req.query.vcpSource)),
      swingShort: getScreener(dataDir, 'swing-short', date, parseSource(req.query.swingSource)),
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
    // Read the data-dir copy first — that is the file the skill scripts use
    // ($TRADING_DATE_DIR/trading_profile.json), keeping the UI in lock-step
    // with what a recalc/scheduler run actually consumes.
    res.json(readProfileFile(dataDir));
  });

  // Edit the trading profile. Validates against PROFILE_SPEC (ranges mirror the
  // consuming scripts), merges over the on-disk profile (partial submit / legacy
  // keys survive), atomically writes the canonical file, and reports which keys
  // changed and which warrant a watchlist/non-active-thesis recalc.
  r.put('/profile', (req, res) => {
    const existing = readProfileFile(dataDir);
    const built = validateProfilePatch(req.body, existing);
    if ('error' in built) {
      const body: SaveProfileResponse = { ok: false, error: built.error };
      return res.status(400).json(body);
    }
    try {
      writeProfileFile(dataDir, built.profile);
    } catch (e) {
      const body: SaveProfileResponse = {
        ok: false,
        error: e instanceof Error ? e.message : 'failed to write trading_profile.json',
      };
      return res.status(500).json(body);
    }
    return res.json(buildSaveResponse(existing, built.profile));
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
      cronLogTail: tailLines(path.join(dataDir, 'logs', 'autopilot_cron.log'), 200),
    };
    res.json(body);
  });

  return r;
}
