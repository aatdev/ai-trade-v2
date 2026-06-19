import fs from 'node:fs';
import path from 'node:path';
import { Router } from 'express';
import { basename, findLatest, readJson } from '../lib/files';
import { resolvePythonBin, startAndRespond } from '../lib/jobActions';
import type { JobManager } from '../lib/jobs';
import {
  RE,
  getExposureGate,
  getPortfolio,
  mapBottomFlowResult,
  mapScreenerResult,
  readProfile,
} from '../lib/mappers';
import { mapStagedPlan, mapStagedScreener } from '../lib/screenerMappers';
import type {
  StagedBottomFlowResponse,
  StagedScreenerResponse,
  StagedShortScreenerResponse,
} from '@shared/types';

const SCREEN_SCRIPT = 'skills/vcp-screener/scripts/screen_vcp.py';
const PLAN_SCRIPT = 'skills/breakout-trade-planner/scripts/plan_breakout_trades.py';
const SAVE_SCRIPT = 'scripts/build_watchlist_from_plan.py';
const SHORT_SCREEN_SCRIPT = 'skills/swing-short-screener/scripts/screen_short.py';
const BOTTOM_FLOW_SCREEN_SCRIPT =
  'skills/bottom-flow-divergence-screener/scripts/screen_bottom_flow.py';
const BOTTOM_FLOW_GRADES = ['A', 'B-accum', 'B-fund'];
const VCP_UNIVERSE_FILE = path.join('scripts', 'lib', 'data', 'vcp_universe.txt');
const STAGING_SUBDIR = 'ui-staging';

const TICKER_RE = /^[A-Z0-9.\-]{1,10}$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const SOURCE_RE = /^[A-Za-z0-9_.\-]{1,80}\.json$/;

const todayISO = (): string => new Date().toISOString().slice(0, 10);

/** Tickers from the optional expanded VCP universe file (one per line, `#` = comment). */
function readWideUniverse(projectRoot: string): string[] {
  let text: string;
  try {
    text = fs.readFileSync(path.join(projectRoot, VCP_UNIVERSE_FILE), 'utf8');
  } catch {
    return [];
  }
  const out = new Set<string>();
  for (const raw of text.split(/\r?\n/)) {
    const sym = raw.split('#')[0].trim().toUpperCase();
    if (TICKER_RE.test(sym)) out.add(sym);
  }
  return [...out];
}

/**
 * Validate the screener-run body and build the screen_vcp.py CLI args. Ranges
 * mirror screen_vcp.py's argparse so a server 400 equals what the script would
 * reject. `--top 100` and `--output-dir` are always server-controlled. Pure +
 * exported for unit tests.
 */
export function buildScreenArgs(
  body: Record<string, unknown>,
  stagingDir: string,
  wideSymbols: string[] | null,
): { args: string[] } | { error: string } {
  const args: string[] = ['--top', '100', '--output-dir', stagingDir];

  const universe = body.universe;
  if (universe !== 'sp500' && universe !== 'wide' && universe !== 'custom') {
    return { error: 'universe must be one of sp500 | wide | custom' };
  }

  // [bodyKey, cliFlag, lo, hi, integer]
  const nums: [string, string, number, number, boolean][] = [
    ['minAtrPct', '--min-atr-pct', 0, 20, false],
    ['trendMinScore', '--trend-min-score', 0, 100, false],
    ['breakoutVolumeRatio', '--breakout-volume-ratio', 0.5, 10, false],
    ['minContractions', '--min-contractions', 2, 4, true],
    ['extThreshold', '--ext-threshold', 0, 50, false],
  ];
  for (const [key, flag, lo, hi, integer] of nums) {
    const v = body[key];
    if (v === undefined || v === null || v === '') continue;
    if (typeof v !== 'number' || !Number.isFinite(v) || v < lo || v > hi) {
      return { error: `${key} must be a number in [${lo}, ${hi}]` };
    }
    if (integer && !Number.isInteger(v)) return { error: `${key} must be an integer` };
    args.push(flag, String(v));
  }

  const mode = body.mode;
  if (mode !== undefined && mode !== null && mode !== '') {
    if (mode !== 'all' && mode !== 'prebreakout') return { error: 'mode must be all | prebreakout' };
    args.push('--mode', mode);
  }
  if (body.strict === true) args.push('--strict');

  // max-candidates is optional in every mode. Empty = analyze all (the universe
  // size for an explicit list; the screener's own default for S&P 500). A number
  // caps how many pre-filter survivors get the full VCP analysis.
  let maxCandidates: number | null = null;
  const mc = body.maxCandidates;
  if (mc !== undefined && mc !== null && mc !== '') {
    if (typeof mc !== 'number' || !Number.isInteger(mc) || mc < 1 || mc > 2000) {
      return { error: 'maxCandidates must be an integer in [1, 2000]' };
    }
    maxCandidates = mc;
  }

  if (universe === 'sp500') {
    if (maxCandidates != null) args.push('--max-candidates', String(maxCandidates));
    return { args };
  }

  // wide / custom pass an explicit --universe; default --max-candidates to the
  // universe size so every chosen name gets full analysis (mirrors the
  // scheduler), but honour an explicit lower cap when the user sets one.
  let symbols: string[];
  if (universe === 'wide') {
    if (!wideSymbols || wideSymbols.length === 0) {
      return { error: `wide universe file (${VCP_UNIVERSE_FILE}) is missing or empty` };
    }
    symbols = wideSymbols;
  } else {
    const raw = Array.isArray(body.symbols) ? body.symbols : [];
    symbols = [
      ...new Set(raw.map((s) => String(s).toUpperCase().trim()).filter((s) => TICKER_RE.test(s))),
    ];
    if (symbols.length === 0) return { error: 'custom universe requires at least one valid ticker' };
    if (symbols.length > 1000) return { error: 'custom universe is capped at 1000 tickers' };
  }
  args.push('--universe', ...symbols, '--max-candidates', String(maxCandidates ?? symbols.length));
  return { args };
}

/**
 * Validate the shorts-run body and build the screen_short.py CLI args. Ranges
 * mirror screen_short.py's argparse so a server 400 equals what the script would
 * reject. `--output-dir` is always server-controlled. Unlike the VCP run, an
 * S&P 500 short scan defaults to the FULL index (`--full-sp500`) when no cap is
 * given — alphabetically truncating to the first 100 names would miss most
 * Stage 4 weakness. Pure + exported for unit tests.
 */
export function buildShortScreenArgs(
  body: Record<string, unknown>,
  stagingDir: string,
  wideSymbols: string[] | null,
): { args: string[] } | { error: string } {
  const args: string[] = ['--output-dir', stagingDir];

  const universe = body.universe;
  if (universe !== 'sp500' && universe !== 'wide' && universe !== 'custom') {
    return { error: 'universe must be one of sp500 | wide | custom' };
  }

  const minGrade = body.minGrade;
  if (minGrade !== undefined && minGrade !== null && minGrade !== '') {
    if (minGrade !== 'A' && minGrade !== 'B' && minGrade !== 'C' && minGrade !== 'D') {
      return { error: 'minGrade must be one of A | B | C | D' };
    }
    args.push('--min-grade', minGrade);
  }

  // [bodyKey, cliFlag, lo, hi, integer]. `top` allows 0 (= all rows).
  const nums: [string, string, number, number, boolean][] = [
    ['top', '--top', 0, 500, true],
    ['rsLookback', '--rs-lookback', 5, 252, true],
    ['minPrice', '--min-price', 0, 100_000, false],
    ['minDollarVol', '--min-dollar-vol', 0, 100_000_000_000, false],
    ['minStopPct', '--min-stop-pct', 0, 50, false],
    ['maxStopPct', '--max-stop-pct', 0, 100, false],
  ];
  for (const [key, flag, lo, hi, integer] of nums) {
    const v = body[key];
    if (v === undefined || v === null || v === '') continue;
    if (typeof v !== 'number' || !Number.isFinite(v) || v < lo || v > hi) {
      return { error: `${key} must be a number in [${lo}, ${hi}]` };
    }
    if (integer && !Number.isInteger(v)) return { error: `${key} must be an integer` };
    args.push(flag, String(v));
  }

  let maxCandidates: number | null = null;
  const mc = body.maxCandidates;
  if (mc !== undefined && mc !== null && mc !== '') {
    if (typeof mc !== 'number' || !Number.isInteger(mc) || mc < 1 || mc > 2000) {
      return { error: 'maxCandidates must be an integer in [1, 2000]' };
    }
    maxCandidates = mc;
  }

  if (universe === 'sp500') {
    // No cap → full S&P 500; an explicit cap analyzes the first N names.
    if (maxCandidates != null) args.push('--max-candidates', String(maxCandidates));
    else args.push('--full-sp500');
    return { args };
  }

  let symbols: string[];
  if (universe === 'wide') {
    if (!wideSymbols || wideSymbols.length === 0) {
      return { error: `wide universe file (${VCP_UNIVERSE_FILE}) is missing or empty` };
    }
    symbols = wideSymbols;
  } else {
    const raw = Array.isArray(body.symbols) ? body.symbols : [];
    symbols = [
      ...new Set(raw.map((s) => String(s).toUpperCase().trim()).filter((s) => TICKER_RE.test(s))),
    ];
    if (symbols.length === 0) return { error: 'custom universe requires at least one valid ticker' };
    if (symbols.length > 1000) return { error: 'custom universe is capped at 1000 tickers' };
  }
  args.push('--universe', ...symbols, '--max-candidates', String(maxCandidates ?? symbols.length));
  return { args };
}

/**
 * Validate the bottom-flow-run body and build the screen_bottom_flow.py CLI args.
 * Ranges mirror the script's argparse so a server 400 equals what the script would
 * reject. `--output-dir` is always server-controlled. Unlike VCP/short there is no
 * sp500/wide/custom universe — the screener scans the whole TradingView market in
 * one POST; `--universe` here means common vs common+preferred. Pure + exported
 * for unit tests.
 */
export function buildBottomFlowArgs(
  body: Record<string, unknown>,
  stagingDir: string,
): { args: string[] } | { error: string } {
  const args: string[] = ['--output-dir', stagingDir];

  const universe = body.universe;
  if (universe !== undefined && universe !== null && universe !== '') {
    if (universe !== 'common' && universe !== 'all') {
      return { error: 'universe must be common | all' };
    }
    args.push('--universe', universe);
  }

  const grades = body.grades;
  if (grades !== undefined && grades !== null && grades !== '') {
    if (typeof grades !== 'string') return { error: 'grades must be a comma-separated string' };
    const toks = grades
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
    if (toks.length === 0 || !toks.every((t) => BOTTOM_FLOW_GRADES.includes(t))) {
      return { error: 'grades must be a comma list of A | B-accum | B-fund' };
    }
    args.push('--grades', toks.join(','));
  }

  if (body.requireTurn === true) args.push('--require-turn');
  if (body.requireSurvivable === true) args.push('--require-survivable');

  // [bodyKey, cliFlag, lo, hi, integer]. `top` allows 0 (= all rows).
  const nums: [string, string, number, number, boolean][] = [
    ['nearLowPct', '--near-low-pct', 0, 100, false],
    ['minDrawdownPct', '--min-drawdown-pct', 0, 100, false],
    ['revTtmMin', '--rev-ttm-min', -100, 1000, false],
    ['mfiMin', '--mfi-min', 0, 100, false],
    ['maxPerf1y', '--max-perf-1y', -100, 0, false],
    ['minCap', '--min-cap', 0, 10_000_000_000_000, false],
    ['minAvgVol', '--min-avg-vol', 0, 10_000_000_000, false],
    ['minPrice', '--min-price', 0, 100_000, false],
    ['top', '--top', 0, 500, true],
  ];
  for (const [key, flag, lo, hi, integer] of nums) {
    const v = body[key];
    if (v === undefined || v === null || v === '') continue;
    if (typeof v !== 'number' || !Number.isFinite(v) || v < lo || v > hi) {
      return { error: `${key} must be a number in [${lo}, ${hi}]` };
    }
    if (integer && !Number.isInteger(v)) return { error: `${key} must be an integer` };
    args.push(flag, String(v));
  }
  return { args };
}

/** A provided file pin must be a well-formed basename of the expected kind. */
function pinError(source: unknown, re: RegExp): string | null {
  if (source === undefined || source === null) return null;
  if (typeof source !== 'string' || !SOURCE_RE.test(source) || !re.test(source)) {
    return 'invalid file pin';
  }
  return null;
}

/** Resolve a staged file: a validated pin (constrained to the staging dir), else newest. */
function resolveStaged(stagingDir: string, re: RegExp, source: unknown): string | null {
  if (typeof source === 'string' && SOURCE_RE.test(source) && re.test(source)) {
    const p = path.join(stagingDir, source);
    return fs.existsSync(p) ? p : null;
  }
  return findLatest(stagingDir, re, null);
}

export function screenerRouter(projectRoot: string, dataDir: string, jobs: JobManager): Router {
  const r = Router();
  const stagingDir = path.join(dataDir, STAGING_SUBDIR);

  // 5.1 — run the VCP screener into staging (NOT registered until save).
  r.post('/screener/run', (req, res) => {
    const body = (req.body ?? {}) as Record<string, unknown>;
    const wide = body.universe === 'wide' ? readWideUniverse(projectRoot) : null;
    const built = buildScreenArgs(body, stagingDir, wide);
    if ('error' in built) return res.status(400).json({ ok: false, error: built.error });
    return startAndRespond(res, jobs, {
      label: `screener run (${String(body.universe)})`,
      cmd: resolvePythonBin(),
      args: [SCREEN_SCRIPT, ...built.args],
      cwd: projectRoot,
      env: { TRADING_DATE_DIR: dataDir, TV_NO_CACHE: '1' },
      lane: 'screener',
      meta: { kind: 'screener-run' },
    });
  });

  // 5.4 — build the breakout trade plan from the staged screener.
  r.post('/screener/plan', (req, res) => {
    const body = (req.body ?? {}) as Record<string, unknown>;
    const pinErr = pinError(body.vcpFile, RE.vcp);
    if (pinErr) return res.status(400).json({ ok: false, error: pinErr });

    const vcpFile = resolveStaged(stagingDir, RE.vcp, body.vcpFile);
    if (!vcpFile) {
      return res.status(409).json({ ok: false, error: 'no staged screener — run the screener first' });
    }
    // The planner hard-requires an account size (profile or --account-size).
    if (!readProfile(dataDir)) {
      return res
        .status(400)
        .json({ ok: false, error: 'no trading_profile.json (account_size required for the planner)' });
    }

    const args = [PLAN_SCRIPT, '--input', vcpFile, '--output-dir', stagingDir];
    const heatFile = findLatest(path.join(dataDir, 'journal'), RE.portfolioHeat, null);
    if (heatFile) args.push('--current-exposure-json', heatFile);
    // Only override the profile's earnings_gate_days when explicitly provided.
    const gate = body.earningsGateDays;
    if (gate !== undefined && gate !== null && gate !== '') {
      if (typeof gate !== 'number' || !Number.isInteger(gate) || gate < 0 || gate > 60) {
        return res.status(400).json({ ok: false, error: 'earningsGateDays must be an integer in [0, 60]' });
      }
      args.push('--earnings-gate-days', String(gate));
    }
    return startAndRespond(res, jobs, {
      label: 'screener plan (breakout-trade-planner)',
      cmd: resolvePythonBin(),
      args,
      cwd: projectRoot,
      env: { TRADING_DATE_DIR: dataDir, TV_NO_CACHE: '1' },
      lane: 'screener',
      meta: { kind: 'screener-plan' },
    });
  });

  // The staged view the UI polls after each job ends. Read-only; no registration.
  r.get('/screener/staged', (req, res) => {
    const vcpFile = resolveStaged(stagingDir, RE.vcp, req.query.vcpSource);
    const planFile = resolveStaged(stagingDir, RE.plan, req.query.planSource);
    const gate = getExposureGate(dataDir, null);
    const heat = getPortfolio(dataDir, null);
    const notes: string[] = [];

    const plan = planFile ? mapStagedPlan(readJson(planFile)) : null;
    const screener = vcpFile
      ? mapStagedScreener(readJson(vcpFile), basename(vcpFile), plan, gate, heat)
      : null;
    if (screener && heat.data == null) {
      notes.push('Нет portfolio-heat — пункт 7 чек-листа (heat/позиции) не определён.');
    }
    if (screener && !plan) {
      notes.push('План не построен — пункты 6–7 чек-листа определятся после «Построить план».');
    }

    const wideCount = readWideUniverse(projectRoot).length;
    const body: StagedScreenerResponse = {
      screener,
      planSource: basename(planFile),
      plan,
      gate,
      heat,
      wideUniverse: { available: wideCount > 0, count: wideCount },
      notes,
    };
    res.json(body);
  });

  // The ONLY endpoint that writes under canonical dirs (watchlist + promote [+ 5.5]).
  r.post('/screener/save-watchlist', (req, res) => {
    const body = (req.body ?? {}) as Record<string, unknown>;
    const mode = body.mode === 'full' ? 'full' : body.mode === 'plain' ? 'plain' : null;
    if (!mode) return res.status(400).json({ ok: false, error: 'mode must be plain | full' });

    const vcpPinErr = pinError(body.vcpFile, RE.vcp);
    if (vcpPinErr) return res.status(400).json({ ok: false, error: vcpPinErr });
    const planPinErr = pinError(body.planFile, RE.plan);
    if (planPinErr) return res.status(400).json({ ok: false, error: planPinErr });

    const vcpFile = resolveStaged(stagingDir, RE.vcp, body.vcpFile);
    if (!vcpFile) {
      return res.status(409).json({ ok: false, error: 'no staged screener to save' });
    }
    const planFile = resolveStaged(stagingDir, RE.plan, body.planFile);

    const date =
      typeof body.date === 'string' && DATE_RE.test(body.date) ? body.date : todayISO();

    const args = [SAVE_SCRIPT, '--staged-vcp', vcpFile, '--date', date, '--promote'];
    if (planFile) args.push('--staged-plan', planFile);
    if (mode === 'full') args.push('--ingest-theses', '--sync-alerts');

    return startAndRespond(res, jobs, {
      label: `save-watchlist (${mode})`,
      cmd: resolvePythonBin(),
      args,
      cwd: projectRoot,
      env: { TRADING_DATE_DIR: dataDir, CLAUDE_TRADING_SKILLS_REPO: projectRoot },
      lane: 'screener',
      meta: { kind: 'screener-save', mode },
    });
  });

  // --- Swing-short screener (shorts sub-tab) ------------------------------
  // Detection-only run into staging. There is no short-side plan/save step:
  // swing-short-screener already carries grade + trade_levels, and the
  // long-only breakout planner / build_watchlist_from_plan cannot consume it.

  r.post('/screener/shorts/run', (req, res) => {
    const body = (req.body ?? {}) as Record<string, unknown>;
    const wide = body.universe === 'wide' ? readWideUniverse(projectRoot) : null;
    const built = buildShortScreenArgs(body, stagingDir, wide);
    if ('error' in built) return res.status(400).json({ ok: false, error: built.error });
    return startAndRespond(res, jobs, {
      label: `short screener run (${String(body.universe)})`,
      cmd: resolvePythonBin(),
      args: [SHORT_SCREEN_SCRIPT, ...built.args],
      cwd: projectRoot,
      env: { TRADING_DATE_DIR: dataDir, TV_NO_CACHE: '1' },
      lane: 'screener',
      meta: { kind: 'short-screener-run' },
    });
  });

  // The staged shorts view the UI polls after a run ends. Read-only; no registration.
  r.get('/screener/shorts/staged', (req, res) => {
    const file = resolveStaged(stagingDir, RE.swingShort, req.query.swingSource);
    const gate = getExposureGate(dataDir, null);
    const screener = file ? mapScreenerResult(readJson(file), 'swing-short') : null;
    const wideCount = readWideUniverse(projectRoot).length;
    const body: StagedShortScreenerResponse = {
      screener,
      source: basename(file),
      gate,
      wideUniverse: { available: wideCount > 0, count: wideCount },
      notes: [],
    };
    res.json(body);
  });

  // --- Bottom flow divergence screener ("дно" sub-tab) --------------------
  // Detection-only run into staging. Like the shorts tab there is no plan/save
  // step — the screener is a discovery tool (no trade levels). Data comes from
  // the public scanner.tradingview.com endpoint (no API key, no TV Desktop).

  r.post('/screener/bottom-flow/run', (req, res) => {
    const body = (req.body ?? {}) as Record<string, unknown>;
    const built = buildBottomFlowArgs(body, stagingDir);
    if ('error' in built) return res.status(400).json({ ok: false, error: built.error });
    return startAndRespond(res, jobs, {
      label: 'bottom-flow screener run',
      cmd: resolvePythonBin(),
      args: [BOTTOM_FLOW_SCREEN_SCRIPT, ...built.args],
      cwd: projectRoot,
      env: { TRADING_DATE_DIR: dataDir },
      lane: 'screener',
      meta: { kind: 'bottom-flow-screener-run' },
    });
  });

  // The staged bottom-flow view the UI polls after a run ends. Read-only.
  r.get('/screener/bottom-flow/staged', (req, res) => {
    const file = resolveStaged(stagingDir, RE.bottomFlow, req.query.bottomFlowSource);
    const gate = getExposureGate(dataDir, null);
    const screener = file ? mapBottomFlowResult(readJson(file)) : null;
    const body: StagedBottomFlowResponse = {
      screener,
      source: basename(file),
      gate,
      notes: [],
    };
    res.json(body);
  });

  return r;
}
