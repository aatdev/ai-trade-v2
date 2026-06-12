import path from 'node:path';
import request from 'supertest';
import { beforeEach, describe, expect, it } from 'vitest';
import { createApp } from '../app';
import { clearListCache } from '../lib/files';

const FIXTURE = path.resolve(process.cwd(), 'test/fixture');
const app = createApp({ dataDir: FIXTURE, projectRoot: path.resolve(process.cwd()) });

beforeEach(() => clearListCache());

describe('GET /api/dates', () => {
  it('lists dates with the newest as latest', async () => {
    const res = await request(app).get('/api/dates');
    expect(res.status).toBe(200);
    expect(res.body.latest).toBe('2026-06-11');
    expect(res.body.dates).toContain('2026-06-10');
  });
});

describe('GET /api/exposure', () => {
  it('resolves the latest gate by default', async () => {
    const res = await request(app).get('/api/exposure');
    expect(res.body.gate.data.decision).toBe('restrict');
    expect(res.body.gate.data.net_exposure_ceiling_pct).toBe(59);
    expect(res.body.posture.data.bias).toBe('NEUTRAL');
  });

  it('honors ?date=', async () => {
    const res = await request(app).get('/api/exposure?date=2026-06-10');
    expect(res.body.gate.data.decision).toBe('allow');
  });
});

describe('GET /api/watchlist', () => {
  it('maps candidates and enriches validated from verdicts', async () => {
    const res = await request(app).get('/api/watchlist');
    const wl = res.body.data;
    expect(wl.candidates).toHaveLength(2);
    const aos = wl.candidates.find((c: { ticker: string }) => c.ticker === 'AOS');
    expect(aos.validated).toBe(true); // enriched from validation verdicts
    expect(wl.rejected_by_validation[0].ticker).toBe('ADSK');
  });
});

describe('GET /api/portfolio', () => {
  it('returns the most recent heat snapshot of the day', async () => {
    const res = await request(app).get('/api/portfolio');
    expect(res.body.data.positions_count).toBe(1);
    expect(res.body.data.positions[0].ticker).toBe('ALLE');
  });
});

describe('GET /api/market', () => {
  it('returns the latest breadth composite and its components', async () => {
    const res = await request(app).get('/api/market');
    expect(res.body.breadth.data.composite_score).toBe(46.2);
    expect(res.body.breadth.data.components.length).toBe(2);
    expect(res.body.posture.data.composite_score).toBe(57);
  });
});

describe('GET /api/screeners', () => {
  it('normalizes swing-short trade levels', async () => {
    const res = await request(app).get('/api/screeners');
    const c = res.body.swingShort.data.candidates[0];
    expect(c.symbol).toBe('ADSK');
    expect(c.entry).toBe(205.57);
    expect(c.target).toBe(114.39);
  });
});

describe('GET /api/theses', () => {
  it('flags past-due reviews', async () => {
    const res = await request(app).get('/api/theses');
    const aapl = res.body.theses.find((t: { ticker: string }) => t.ticker === 'AAPL');
    const googl = res.body.theses.find((t: { ticker: string }) => t.ticker === 'GOOGL');
    expect(aapl.review_due).toBe(true);
    expect(googl.review_due).toBe(false);
  });
});

describe('GET /api/analysis/tickers', () => {
  it('lists tickers that have saved analysis with their latest date', async () => {
    const res = await request(app).get('/api/analysis/tickers');
    expect(res.status).toBe(200);
    expect(res.body.tickers.AAPL).toEqual({ latest: '2026-06-11', count: 1 });
    // signals.md is a file, not a ticker dir — must not appear
    expect(Object.keys(res.body.tickers)).not.toContain('signals');
  });
});

describe('GET /api/profile & /api/signals', () => {
  it('reads the profile and signals journal', async () => {
    const profile = await request(app).get('/api/profile');
    expect(profile.body.account_size).toBe(150000);
    const signals = await request(app).get('/api/signals');
    expect(signals.body.present).toBe(true);
    expect(signals.body.content).toContain('ALB');
  });
});
