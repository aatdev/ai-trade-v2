import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import request from 'supertest';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { createApp } from '../app';
import { clearListCache } from '../lib/files';
import { buildBottomFlowArgs, buildScreenArgs, buildShortScreenArgs } from './screener';

const FIXTURE = path.resolve(process.cwd(), 'test/fixture');
const ROOT = path.resolve(process.cwd());
const app = createApp({ dataDir: FIXTURE, projectRoot: ROOT });
const STAGING = '/data/ui-staging';

/* ---------------- buildScreenArgs (pure) ---------------- */

describe('buildScreenArgs', () => {
  it('always forces --top 100 and --output-dir for sp500 defaults', () => {
    const out = buildScreenArgs({ universe: 'sp500' }, STAGING, null);
    expect(out).toEqual({ args: ['--top', '100', '--output-dir', STAGING] });
  });

  it('rejects an unknown universe', () => {
    expect('error' in buildScreenArgs({ universe: 'all-stocks' }, STAGING, null)).toBe(true);
  });

  it('validates numeric ranges (minContractions, breakoutVolumeRatio, mode)', () => {
    expect('error' in buildScreenArgs({ universe: 'sp500', minContractions: 5 }, STAGING, null)).toBe(true);
    expect('error' in buildScreenArgs({ universe: 'sp500', minContractions: 2.5 }, STAGING, null)).toBe(true);
    expect('error' in buildScreenArgs({ universe: 'sp500', breakoutVolumeRatio: 20 }, STAGING, null)).toBe(true);
    expect('error' in buildScreenArgs({ universe: 'sp500', mode: 'nope' }, STAGING, null)).toBe(true);
  });

  it('passes valid tuning flags + strict + maxCandidates', () => {
    const out = buildScreenArgs(
      { universe: 'sp500', trendMinScore: 88, strict: true, maxCandidates: 250, mode: 'prebreakout' },
      STAGING,
      null,
    );
    if ('error' in out) throw new Error(out.error);
    expect(out.args).toContain('--strict');
    expect(out.args).toEqual(expect.arrayContaining(['--trend-min-score', '88', '--max-candidates', '250', '--mode', 'prebreakout']));
  });

  it('custom: rejects no valid tickers, dedupes + uppercases valid ones', () => {
    expect('error' in buildScreenArgs({ universe: 'custom', symbols: ['not a ticker!'] }, STAGING, null)).toBe(true);
    const out = buildScreenArgs({ universe: 'custom', symbols: ['nvda', 'NVDA', 'avgo'] }, STAGING, null);
    if ('error' in out) throw new Error(out.error);
    expect(out.args).toEqual(expect.arrayContaining(['--universe', 'NVDA', 'AVGO', '--max-candidates', '2']));
  });

  it('wide: errors when the universe file is empty, includes symbols when present', () => {
    expect('error' in buildScreenArgs({ universe: 'wide' }, STAGING, [])).toBe(true);
    const out = buildScreenArgs({ universe: 'wide' }, STAGING, ['AAA', 'BBB']);
    if ('error' in out) throw new Error(out.error);
    expect(out.args).toEqual(expect.arrayContaining(['--universe', 'AAA', 'BBB', '--max-candidates', '2']));
  });

  it('max-candidates: empty omits the flag for S&P 500 (script default applies)', () => {
    const out = buildScreenArgs({ universe: 'sp500' }, STAGING, null);
    if ('error' in out) throw new Error(out.error);
    expect(out.args).not.toContain('--max-candidates');
  });

  it('max-candidates: an explicit value caps an explicit universe below its size', () => {
    const out = buildScreenArgs({ universe: 'custom', symbols: ['A', 'B', 'C'], maxCandidates: 1 }, STAGING, null);
    if ('error' in out) throw new Error(out.error);
    // user cap (1) wins over the universe size (3)
    expect(out.args).toEqual(expect.arrayContaining(['--universe', 'A', 'B', 'C', '--max-candidates', '1']));
  });

  it('max-candidates: rejects out-of-range in any mode', () => {
    expect('error' in buildScreenArgs({ universe: 'custom', symbols: ['A'], maxCandidates: 9999 }, STAGING, null)).toBe(true);
    expect('error' in buildScreenArgs({ universe: 'sp500', maxCandidates: 0 }, STAGING, null)).toBe(true);
  });
});

/* ---------------- buildShortScreenArgs (pure) ---------------- */

describe('buildShortScreenArgs', () => {
  it('sp500 with no cap scans the full index (--full-sp500)', () => {
    const out = buildShortScreenArgs({ universe: 'sp500' }, STAGING, null);
    expect(out).toEqual({ args: ['--output-dir', STAGING, '--full-sp500'] });
  });

  it('sp500 with an explicit cap uses --max-candidates (not --full-sp500)', () => {
    const out = buildShortScreenArgs({ universe: 'sp500', maxCandidates: 120 }, STAGING, null);
    if ('error' in out) throw new Error(out.error);
    expect(out.args).toContain('--max-candidates');
    expect(out.args).toContain('120');
    expect(out.args).not.toContain('--full-sp500');
  });

  it('rejects an unknown universe and a bad grade', () => {
    expect('error' in buildShortScreenArgs({ universe: 'nope' }, STAGING, null)).toBe(true);
    expect('error' in buildShortScreenArgs({ universe: 'sp500', minGrade: 'Z' }, STAGING, null)).toBe(true);
  });

  it('validates numeric ranges (top, rsLookback, stop pct)', () => {
    expect('error' in buildShortScreenArgs({ universe: 'sp500', top: 2.5 }, STAGING, null)).toBe(true);
    expect('error' in buildShortScreenArgs({ universe: 'sp500', rsLookback: 1 }, STAGING, null)).toBe(true);
    expect('error' in buildShortScreenArgs({ universe: 'sp500', maxStopPct: 200 }, STAGING, null)).toBe(true);
  });

  it('passes valid grade + tuning flags', () => {
    const out = buildShortScreenArgs(
      { universe: 'sp500', minGrade: 'B', top: 0, rsLookback: 63, minStopPct: 2, maxStopPct: 10 },
      STAGING,
      null,
    );
    if ('error' in out) throw new Error(out.error);
    expect(out.args).toEqual(
      expect.arrayContaining(['--min-grade', 'B', '--top', '0', '--rs-lookback', '63', '--min-stop-pct', '2', '--max-stop-pct', '10']),
    );
  });

  it('custom: dedupes + uppercases tickers and sizes --max-candidates', () => {
    expect('error' in buildShortScreenArgs({ universe: 'custom', symbols: ['$$$'] }, STAGING, null)).toBe(true);
    const out = buildShortScreenArgs({ universe: 'custom', symbols: ['tsla', 'TSLA', 'nflx'] }, STAGING, null);
    if ('error' in out) throw new Error(out.error);
    expect(out.args).toEqual(expect.arrayContaining(['--universe', 'TSLA', 'NFLX', '--max-candidates', '2']));
  });

  it('wide: errors when the universe file is empty, includes symbols when present', () => {
    expect('error' in buildShortScreenArgs({ universe: 'wide' }, STAGING, [])).toBe(true);
    const out = buildShortScreenArgs({ universe: 'wide' }, STAGING, ['AAA', 'BBB']);
    if ('error' in out) throw new Error(out.error);
    expect(out.args).toEqual(expect.arrayContaining(['--universe', 'AAA', 'BBB', '--max-candidates', '2']));
  });
});

/* ---------------- route guards (no spawn) ---------------- */

async function jobsList() {
  return (await request(app).get('/api/actions/jobs')).body;
}

describe('POST /api/screener/run', () => {
  it('rejects a missing/invalid universe before spawning', async () => {
    const res = await request(app).post('/api/screener/run').send({});
    expect(res.status).toBe(400);
    expect((await jobsList()).activeLanes).toEqual({});
  });

  it('rejects out-of-range tuning before spawning', async () => {
    const res = await request(app).post('/api/screener/run').send({ universe: 'sp500', minContractions: 9 });
    expect(res.status).toBe(400);
    expect((await jobsList()).activeLanes).toEqual({});
  });

  it('rejects wide when the universe file is absent (cwd has none)', async () => {
    const res = await request(app).post('/api/screener/run').send({ universe: 'wide' });
    expect(res.status).toBe(400);
  });
});

describe('POST /api/screener/plan', () => {
  it('409s when no screener has been staged', async () => {
    const res = await request(app).post('/api/screener/plan').send({});
    expect(res.status).toBe(409);
    expect(res.body.ok).toBe(false);
  });

  it('400s on a malformed vcpFile pin', async () => {
    const res = await request(app).post('/api/screener/plan').send({ vcpFile: '../../etc/passwd' });
    expect(res.status).toBe(400);
  });
});

describe('POST /api/screener/save-watchlist', () => {
  it('rejects a bad mode', async () => {
    const res = await request(app).post('/api/screener/save-watchlist').send({ mode: 'nuke' });
    expect(res.status).toBe(400);
  });

  it('rejects a malformed vcpFile pin', async () => {
    const res = await request(app).post('/api/screener/save-watchlist').send({ mode: 'plain', vcpFile: 'x.txt' });
    expect(res.status).toBe(400);
  });

  it('409s when there is nothing staged to save', async () => {
    const res = await request(app).post('/api/screener/save-watchlist').send({ mode: 'plain' });
    expect(res.status).toBe(409);
  });
});

describe('POST /api/screener/shorts/run', () => {
  it('rejects a missing/invalid universe before spawning', async () => {
    const res = await request(app).post('/api/screener/shorts/run').send({});
    expect(res.status).toBe(400);
    expect((await jobsList()).activeLanes).toEqual({});
  });

  it('rejects an invalid grade before spawning', async () => {
    const res = await request(app).post('/api/screener/shorts/run').send({ universe: 'sp500', minGrade: 'Z' });
    expect(res.status).toBe(400);
    expect((await jobsList()).activeLanes).toEqual({});
  });

  it('rejects wide when the universe file is absent (cwd has none)', async () => {
    const res = await request(app).post('/api/screener/shorts/run').send({ universe: 'wide' });
    expect(res.status).toBe(400);
  });
});

describe('GET /api/screener/shorts/staged (no staging)', () => {
  it('returns a null screener with gate + wideUniverse context', async () => {
    const res = await request(app).get('/api/screener/shorts/staged');
    expect(res.status).toBe(200);
    expect(res.body.screener).toBeNull();
    expect(res.body).toHaveProperty('gate');
    expect(res.body.wideUniverse).toEqual({ available: false, count: 0 });
  });
});

describe('GET /api/screener/staged (no staging)', () => {
  it('returns null screener/plan with gate + heat context', async () => {
    const res = await request(app).get('/api/screener/staged');
    expect(res.status).toBe(200);
    expect(res.body.screener).toBeNull();
    expect(res.body.plan).toBeNull();
    expect(res.body).toHaveProperty('gate');
    expect(res.body).toHaveProperty('heat');
    // projectRoot = ui/server (no vcp_universe.txt there) ⇒ wide universe absent.
    expect(res.body.wideUniverse).toEqual({ available: false, count: 0 });
  });
});

/* ---------------- staging isolation (regression) ---------------- */

describe('ui-staging isolation', () => {
  let dir: string;
  let stagedApp: ReturnType<typeof createApp>;

  beforeEach(() => {
    clearListCache();
    dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ui-screener-'));
    const staging = path.join(dir, 'ui-staging');
    fs.mkdirSync(staging, { recursive: true });
    // A far-future date that cannot collide with any real fixture date.
    fs.writeFileSync(
      path.join(staging, 'vcp_screener_2099-01-02_120000.json'),
      JSON.stringify({
        schema_version: '1.0',
        metadata: { generated_at: '2099-01-02 12:00:00', funnel: { universe: 1 } },
        results: [{ symbol: 'NVDA', composite_score: 80, price: 100, valid_vcp: true }],
        summary: { total: 1 },
      }),
    );
    fs.writeFileSync(
      path.join(staging, 'breakout_trade_plan_2099-01-02_120000.json'),
      JSON.stringify({ schema_version: '1.0', summary: {}, actionable_orders: [] }),
    );
    fs.writeFileSync(
      path.join(staging, 'swing_short_screener_2099-01-02_120000.json'),
      JSON.stringify({
        meta: { universe_size: 1, source: 'fixture' },
        candidates: [
          {
            symbol: 'CHTR',
            grade: 'A',
            composite_score: 81,
            sector: 'Communication Services',
            components: { trend_structure: 100 },
            trade_levels: { entry: 145.8, stop: 153.6, target_2r: 130.2 },
            metrics: { price: 145.8, rsi14: 50.8 },
          },
        ],
      }),
    );
    stagedApp = createApp({ dataDir: dir, projectRoot: ROOT });
  });
  afterEach(() => fs.rmSync(dir, { recursive: true, force: true }));

  it('the staged run is invisible to /api/dates and /api/screeners', async () => {
    const dates = await request(stagedApp).get('/api/dates');
    expect(dates.body.dates).not.toContain('2099-01-02');

    const screeners = await request(stagedApp).get('/api/screeners');
    expect(screeners.body.vcp.data).toBeNull();
    expect(screeners.body.swingShort.data).toBeNull();
  });

  it('but IS visible to /api/screener/staged with the top-100 view', async () => {
    const staged = await request(stagedApp).get('/api/screener/staged');
    expect(staged.body.screener).not.toBeNull();
    expect(staged.body.screener.candidates).toHaveLength(1);
    expect(staged.body.screener.candidates[0].symbol).toBe('NVDA');
  });

  it('and the staged shorts run IS visible to /api/screener/shorts/staged', async () => {
    const staged = await request(stagedApp).get('/api/screener/shorts/staged');
    expect(staged.body.screener).not.toBeNull();
    expect(staged.body.screener.kind).toBe('swing-short');
    expect(staged.body.screener.candidates).toHaveLength(1);
    expect(staged.body.screener.candidates[0].symbol).toBe('CHTR');
    // trade_levels normalized into entry/stop/target.
    expect(staged.body.screener.candidates[0].entry).toBe(145.8);
    expect(staged.body.screener.candidates[0].target).toBe(130.2);
  });
});

/* ---------------- buildBottomFlowArgs (pure) ---------------- */

describe('buildBottomFlowArgs', () => {
  it('defaults to only the server-controlled --output-dir', () => {
    expect(buildBottomFlowArgs({}, STAGING)).toEqual({ args: ['--output-dir', STAGING] });
  });

  it('rejects an unknown universe but accepts common | all', () => {
    expect('error' in buildBottomFlowArgs({ universe: 'sp500' }, STAGING)).toBe(true);
    const out = buildBottomFlowArgs({ universe: 'all' }, STAGING);
    if ('error' in out) throw new Error(out.error);
    expect(out.args).toEqual(expect.arrayContaining(['--universe', 'all']));
  });

  it('validates grade tokens and passes a clean comma list', () => {
    expect('error' in buildBottomFlowArgs({ grades: 'A,Z' }, STAGING)).toBe(true);
    expect('error' in buildBottomFlowArgs({ grades: 'B' }, STAGING)).toBe(true); // bare B invalid
    const out = buildBottomFlowArgs({ grades: 'A, B-accum' }, STAGING);
    if ('error' in out) throw new Error(out.error);
    expect(out.args).toEqual(expect.arrayContaining(['--grades', 'A,B-accum']));
  });

  it('adds the boolean gate flags only when true', () => {
    const out = buildBottomFlowArgs({ requireTurn: true, requireSurvivable: false }, STAGING);
    if ('error' in out) throw new Error(out.error);
    expect(out.args).toContain('--require-turn');
    expect(out.args).not.toContain('--require-survivable');
  });

  it('validates numeric ranges (nearLowPct, maxPerf1y, mfiMin, top integer)', () => {
    expect('error' in buildBottomFlowArgs({ nearLowPct: 150 }, STAGING)).toBe(true);
    expect('error' in buildBottomFlowArgs({ maxPerf1y: 5 }, STAGING)).toBe(true); // must be ≤ 0
    expect('error' in buildBottomFlowArgs({ mfiMin: -1 }, STAGING)).toBe(true);
    expect('error' in buildBottomFlowArgs({ top: 2.5 }, STAGING)).toBe(true);
  });

  it('passes valid numeric tuning flags through', () => {
    const out = buildBottomFlowArgs(
      { nearLowPct: 20, minDrawdownPct: 40, mfiMin: 55, maxPerf1y: -15, minCap: 2e9, top: 30 },
      STAGING,
    );
    if ('error' in out) throw new Error(out.error);
    expect(out.args).toEqual(
      expect.arrayContaining([
        '--near-low-pct', '20',
        '--min-drawdown-pct', '40',
        '--mfi-min', '55',
        '--max-perf-1y', '-15',
        '--min-cap', '2000000000',
        '--top', '30',
      ]),
    );
  });
});
