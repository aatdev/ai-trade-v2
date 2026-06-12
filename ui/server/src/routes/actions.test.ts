import path from 'node:path';
import request from 'supertest';
import { describe, expect, it } from 'vitest';
import { createApp } from '../app';
import { buildMemoryArgs } from '../lib/memoryOps';

const FIXTURE = path.resolve(process.cwd(), 'test/fixture');
const app = createApp({ dataDir: FIXTURE, projectRoot: path.resolve(process.cwd()) });

describe('POST /api/actions/run-slot', () => {
  it('rejects a non-whitelisted slot before spawning anything', async () => {
    const res = await request(app).post('/api/actions/run-slot').send({ slot: 'rm-rf' });
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
  it('starts empty with no active job', async () => {
    const res = await request(app).get('/api/actions/jobs');
    expect(res.body.jobs).toEqual([]);
    expect(res.body.active).toBeNull();
  });
});
