import path from 'node:path';
import request from 'supertest';
import { describe, expect, it } from 'vitest';
import { createApp } from '../app';

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
