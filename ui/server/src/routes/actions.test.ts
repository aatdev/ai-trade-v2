import path from 'node:path';
import request from 'supertest';
import { describe, expect, it } from 'vitest';
import { createApp } from '../app';
import { JobManager } from '../lib/jobs';
import { buildMemoryArgs, buildDeleteThesesArgs } from '../lib/memoryOps';

const FIXTURE = path.resolve(process.cwd(), 'test/fixture');
const app = createApp({ dataDir: FIXTURE, projectRoot: path.resolve(process.cwd()) });

describe('POST /api/actions/run-slot', () => {
  it('rejects a non-whitelisted slot before spawning anything', async () => {
    const res = await request(app).post('/api/actions/run-slot').send({ slot: 'rm-rf' });
    expect(res.status).toBe(400);
    expect(res.body.ok).toBe(false);
  });
});

describe('POST /api/actions/recalc-profile', () => {
  it('rejects a malformed date before spawning', async () => {
    const res = await request(app).post('/api/actions/recalc-profile').send({ date: '06/16/2026' });
    expect(res.status).toBe(400);
    expect(res.body.ok).toBe(false);
  });
});

describe('POST /api/actions/delete-alerts', () => {
  it('rejects an empty/invalid ticker list', async () => {
    const res = await request(app).post('/api/actions/delete-alerts').send({ tickers: [] });
    expect(res.status).toBe(400);
  });
});

describe('POST /api/actions/analyze-ticker', () => {
  it('rejects an invalid ticker before spawning claude', async () => {
    const res = await request(app).post('/api/actions/analyze-ticker').send({ ticker: 'not a ticker!' });
    expect(res.status).toBe(400);
    expect(res.body.ok).toBe(false);
  });
});

describe('POST /api/actions/memory', () => {
  it('rejects an unknown op before spawning', async () => {
    const res = await request(app).post('/api/actions/memory').send({ op: 'rm-rf' });
    expect(res.status).toBe(400);
    expect(res.body.ok).toBe(false);
  });

  it('rejects an invalid thesis id for delete', async () => {
    const res = await request(app).post('/api/actions/memory').send({ op: 'delete', thesisId: 'not!an!id' });
    expect(res.status).toBe(400);
  });
});

describe('POST /api/actions/delete-theses', () => {
  it('rejects an empty id list', async () => {
    const res = await request(app).post('/api/actions/delete-theses').send({ ids: [] });
    expect(res.status).toBe(400);
  });

  it('rejects a non-deletable (OPEN) thesis from the index', async () => {
    // th_googl_pvt_20260602_8f12 is OPEN in the fixture index — not bulk-deletable.
    const res = await request(app)
      .post('/api/actions/delete-theses')
      .send({ ids: ['th_googl_pvt_20260602_8f12'] });
    expect(res.status).toBe(400);
    expect(res.body.ok).toBe(false);
  });

  it('rejects a bad-format and an unknown id before spawning', async () => {
    expect((await request(app).post('/api/actions/delete-theses').send({ ids: ['not!an!id'] })).status).toBe(400);
    expect(
      (await request(app).post('/api/actions/delete-theses').send({ ids: ['th_zzz_x_20260101_0000'] })).status,
    ).toBe(400);
  });
});

describe('POST /api/actions/place-ib-bracket', () => {
  it('rejects an invalid thesis id before spawning', async () => {
    const res = await request(app).post('/api/actions/place-ib-bracket').send({ thesisId: 'not!an!id' });
    expect(res.status).toBe(400);
    expect(res.body.ok).toBe(false);
  });

  it('rejects a well-formed but unknown thesis id', async () => {
    const res = await request(app)
      .post('/api/actions/place-ib-bracket')
      .send({ thesisId: 'th_absent_x_20260101_9999', quantity: 10 });
    expect(res.status).toBe(400);
  });

  it('refuses a thesis that is not ENTRY_READY (fixture AAPL is IDEA)', async () => {
    const res = await request(app)
      .post('/api/actions/place-ib-bracket')
      .send({ thesisId: 'th_aapl_pvt_20260602_2c8b', quantity: 10 });
    expect(res.status).toBe(400);
    expect(String(res.body.error)).toContain('ENTRY_READY');
  });
});

describe('POST /api/actions/cancel-ib-bracket', () => {
  it('rejects an invalid thesis id before spawning', async () => {
    const res = await request(app).post('/api/actions/cancel-ib-bracket').send({ thesisId: 'nope' });
    expect(res.status).toBe(400);
    expect(res.body.ok).toBe(false);
  });
});

describe('POST /api/actions/cancel-ib-order', () => {
  it('rejects an empty / invalid order-id list before spawning', async () => {
    expect((await request(app).post('/api/actions/cancel-ib-order').send({ orderIds: [] })).status).toBe(400);
    expect(
      (await request(app).post('/api/actions/cancel-ib-order').send({ orderIds: ['bad id!'] })).status,
    ).toBe(400);
  });
});

describe('buildDeleteThesesArgs', () => {
  const SD = '/data/journal/theses';
  const STATUS: Record<string, string> = {
    th_a_x_20260101_0001: 'IDEA',
    th_b_x_20260101_0002: 'INVALIDATED',
    th_c_x_20260101_0003: 'ENTRY_READY',
    th_open_x_20260101_0004: 'ACTIVE',
  };

  it('builds a multi-id delete for deletable statuses', () => {
    expect(
      buildDeleteThesesArgs(['th_a_x_20260101_0001', 'th_b_x_20260101_0002', 'th_c_x_20260101_0003'], STATUS, SD),
    ).toEqual({
      args: ['store', '--state-dir', SD, 'delete', 'th_a_x_20260101_0001', 'th_b_x_20260101_0002', 'th_c_x_20260101_0003'],
      label: 'memory: delete 3 thesis/theses',
    });
  });

  it('de-duplicates repeated ids', () => {
    expect(buildDeleteThesesArgs(['th_a_x_20260101_0001', 'th_a_x_20260101_0001'], STATUS, SD)).toEqual({
      args: ['store', '--state-dir', SD, 'delete', 'th_a_x_20260101_0001'],
      label: 'memory: delete 1 thesis/theses',
    });
  });

  it('rejects empty, bad-format, unknown, and non-deletable (ACTIVE) ids', () => {
    expect('error' in buildDeleteThesesArgs([], STATUS, SD)).toBe(true);
    expect('error' in buildDeleteThesesArgs('nope', STATUS, SD)).toBe(true);
    expect('error' in buildDeleteThesesArgs(['not!an!id'], STATUS, SD)).toBe(true);
    expect('error' in buildDeleteThesesArgs(['th_missing_x_20260101_9999'], STATUS, SD)).toBe(true);
    expect('error' in buildDeleteThesesArgs(['th_open_x_20260101_0004'], STATUS, SD)).toBe(true);
  });
});

describe('buildMemoryArgs', () => {
  const SD = '/data/journal/theses';

  it('builds review-due / summary with the state dir', () => {
    expect(buildMemoryArgs({ op: 'review-due' }, SD)).toEqual({
      args: ['review', '--state-dir', SD, 'review-due'],
      label: 'memory: review-due',
    });
  });

  it('builds a validated transition', () => {
    const out = buildMemoryArgs(
      { op: 'transition', thesisId: 'th_aapl_pvt_20260602_2c8b', newStatus: 'entry_ready', reason: 'base formed' },
      SD,
    );
    expect(out).toEqual({
      args: ['store', '--state-dir', SD, 'transition', 'th_aapl_pvt_20260602_2c8b', 'ENTRY_READY', '--reason', 'base formed'],
      label: 'memory: th_aapl_pvt_20260602_2c8b → ENTRY_READY',
    });
  });

  it('rejects a bad status, bad price, and traversal in ingest input', () => {
    expect('error' in buildMemoryArgs({ op: 'transition', thesisId: 'th_x_y_20260101_0000', newStatus: 'NOPE', reason: 'r' }, SD)).toBe(true);
    expect('error' in buildMemoryArgs({ op: 'close', thesisId: 'th_x_y_20260101_0000', exitReason: 'manual', price: -1, date: '2026-01-01' }, SD)).toBe(true);
    expect('error' in buildMemoryArgs({ op: 'ingest', source: 'vcp-screener', input: '../../etc/passwd.json' }, SD)).toBe(true);
  });

  it('builds an ingest with source + relative json input', () => {
    expect(buildMemoryArgs({ op: 'ingest', source: 'vcp-screener', input: 'reports/vcp_2026.json' }, SD)).toEqual({
      args: ['ingest', '--state-dir', SD, '--source', 'vcp-screener', '--input', 'reports/vcp_2026.json'],
      label: 'memory: ingest vcp-screener',
    });
  });

  const ID = 'th_aapl_pvt_20260602_2c8b';

  it('builds a trim with shares, price and date', () => {
    expect(
      buildMemoryArgs({ op: 'trim', thesisId: ID, sharesSold: 4, price: 120, date: '2026-05-10' }, SD),
    ).toEqual({
      args: ['store', '--state-dir', SD, 'trim', ID, '--shares-sold', '4', '--price', '120', '--date', '2026-05-10'],
      label: `memory: trim ${ID}`,
    });
  });

  it('open-position carries optional shares + event-date, rejects bad date', () => {
    expect(
      buildMemoryArgs(
        { op: 'open-position', thesisId: ID, price: 100, date: '2026-05-01', shares: 50, eventDate: '2026-05-02' },
        SD,
      ),
    ).toEqual({
      args: ['store', '--state-dir', SD, 'open-position', ID, '--actual-price', '100', '--actual-date', '2026-05-01', '--shares', '50', '--event-date', '2026-05-02'],
      label: `memory: open ${ID}`,
    });
    expect('error' in buildMemoryArgs({ op: 'open-position', thesisId: ID, price: 100, date: 'nope' }, SD)).toBe(true);
  });

  it('attach-position requires a relative json report (rejects traversal)', () => {
    expect('error' in buildMemoryArgs({ op: 'attach-position', thesisId: ID, report: '../x.json' }, SD)).toBe(true);
    expect(buildMemoryArgs({ op: 'attach-position', thesisId: ID, report: 'reports/sz.json', expectedEntry: 10 }, SD)).toEqual({
      args: ['store', '--state-dir', SD, 'attach-position', ID, '--report', 'reports/sz.json', '--expected-entry', '10'],
      label: `memory: attach-position ${ID}`,
    });
  });

  it('terminate maps reason → --exit-reason and validates terminal status', () => {
    expect(
      buildMemoryArgs({ op: 'terminate', thesisId: ID, terminalStatus: 'invalidated', exitReason: 'thesis broke' }, SD),
    ).toEqual({
      args: ['store', '--state-dir', SD, 'terminate', ID, '--terminal-status', 'INVALIDATED', '--exit-reason', 'thesis broke'],
      label: `memory: terminate ${ID} (INVALIDATED)`,
    });
    expect('error' in buildMemoryArgs({ op: 'terminate', thesisId: ID, terminalStatus: 'ACTIVE', exitReason: 'x' }, SD)).toBe(true);
  });

  it('builds heat with optional numeric flags and json-only', () => {
    expect(buildMemoryArgs({ op: 'heat', accountSize: 150000, jsonOnly: true }, SD)).toEqual({
      args: ['heat', '--state-dir', SD, '--account-size', '150000', '--json-only'],
      label: 'memory: heat',
    });
    expect('error' in buildMemoryArgs({ op: 'heat', maxPositions: 2.5 }, SD)).toBe(true);
  });

  it('list validates filter values', () => {
    expect(buildMemoryArgs({ op: 'list', status: 'active', ticker: 'aapl' }, SD)).toEqual({
      args: ['store', '--state-dir', SD, 'list', '--ticker', 'AAPL', '--status', 'ACTIVE'],
      label: 'memory: list',
    });
    expect('error' in buildMemoryArgs({ op: 'list', status: 'BOGUS' }, SD)).toBe(true);
  });
});

describe('POST /api/actions/jobs/:id/cancel', () => {
  it('409 for an unknown / non-running job', async () => {
    const res = await request(app).post('/api/actions/jobs/nope/cancel');
    expect(res.status).toBe(409);
    expect(res.body.ok).toBe(false);
  });
});

describe('ticker routes guard against traversal', () => {
  it('rejects an invalid symbol', async () => {
    const res = await request(app).get('/api/ticker/A_B%2F..');
    expect(res.status).toBe(400);
  });

  it('rejects an invalid timeframe', async () => {
    const res = await request(app).get('/api/ticker/AAPL/2026-06-11/chart/hourly');
    expect(res.status).toBe(400);
  });
});

describe('GET /api/actions/jobs', () => {
  it('starts empty with no active lanes', async () => {
    const res = await request(app).get('/api/actions/jobs');
    expect(res.body.jobs).toEqual([]);
    expect(res.body.activeLanes).toEqual({});
  });
});

/**
 * Lane WIRING: each whitelisted action must request the correct resource lane
 * so cross-type jobs run concurrently while same-resource jobs serialize. A
 * recording JobManager captures `start()` opts and never spawns — the lane
 * LOCKING semantics themselves live in lib/jobs.test.ts.
 */
describe('action → lane wiring', () => {
  class RecordingJobs extends JobManager {
    calls: Array<Parameters<JobManager['start']>[0]> = [];
    start(opts: Parameters<JobManager['start']>[0]) {
      this.calls.push(opts);
      const job = {
        id: 'job-rec',
        label: opts.label,
        cmd: opts.cmd,
        args: opts.args,
        status: 'running',
        startedAt: 0,
        endedAt: null,
        exitCode: null,
        lines: [],
        proc: null,
        lane: opts.lane,
        meta: opts.meta,
      };
      return { busy: false, job } as unknown as ReturnType<JobManager['start']>;
    }
  }

  async function laneFor(send: (a: ReturnType<typeof request>) => request.Test): Promise<unknown> {
    const jobs = new RecordingJobs();
    const a = createApp({ dataDir: FIXTURE, projectRoot: path.resolve(process.cwd()), jobs });
    const res = await send(request(a));
    expect(res.status).toBe(200);
    return jobs.calls.at(-1)?.lane;
  }

  it('run-slot → scheduler', async () => {
    expect(await laneFor((r) => r.post('/api/actions/run-slot').send({ slot: 'premarket' }))).toBe('scheduler');
  });
  it('analyze-ticker → tradingview', async () => {
    expect(await laneFor((r) => r.post('/api/actions/analyze-ticker').send({ ticker: 'AAPL' }))).toBe('tradingview');
  });
  it('sync-alerts → tradingview', async () => {
    expect(await laneFor((r) => r.post('/api/actions/sync-alerts').send({}))).toBe('tradingview');
  });
  it('sync-ib-fills → ib', async () => {
    expect(await laneFor((r) => r.post('/api/actions/sync-ib-fills').send({}))).toBe('ib');
  });
  it('memory op is lane-less (never blocks)', async () => {
    expect(await laneFor((r) => r.post('/api/actions/memory').send({ op: 'list' }))).toBeUndefined();
  });
  it('screener run → screener', async () => {
    expect(await laneFor((r) => r.post('/api/screener/run').send({ universe: 'sp500' }))).toBe('screener');
  });
});
