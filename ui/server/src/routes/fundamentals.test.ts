import path from 'node:path';
import request from 'supertest';
import { afterEach, describe, expect, it } from 'vitest';
import { createApp } from '../app';

const FIXTURE_DIR = path.resolve(process.cwd(), 'test/fixture');
const FUNDA = path.join(FIXTURE_DIR, 'fundamentals_aapl.json');
const app = createApp({ dataDir: FIXTURE_DIR, projectRoot: path.resolve(process.cwd()) });

afterEach(() => {
  delete process.env.TRADING_UI_FUNDAMENTALS_FIXTURE;
});

describe('GET /api/fundamentals/:symbol', () => {
  it('returns parsed profile + metrics when the fixture is set', async () => {
    process.env.TRADING_UI_FUNDAMENTALS_FIXTURE = FUNDA;
    const res = await request(app).get('/api/fundamentals/NASDAQ:AAPL');
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
    expect(res.body.source).toBe('fixture');
    expect(res.body.symbol).toBe('NASDAQ:AAPL');
    const d = res.body.data;
    expect(d.name).toBe('Apple Inc.');
    expect(d.description).toMatch(/designs, manufactures/);
    expect(d.sector).toBe('Electronic Technology');
    expect(d.country).toBe('United States');
    expect(d.employees).toBe(166000);
    // Valuation + performance round-trip as numbers.
    expect(d.marketCap).toBeGreaterThan(1e12);
    expect(d.peTtm).toBeCloseTo(35.95, 1);
    expect(d.epsTtm).toBeCloseTo(8.2665, 3);
    expect(d.perfW).toBeCloseTo(2.22, 1);
    expect(d.perfY).toBeCloseTo(50.71, 1);
    expect(d.high52w).toBe(317.4);
    expect(d.low52w).toBe(196.855);
  });

  it('accepts an exchange-qualified symbol path param', async () => {
    process.env.TRADING_UI_FUNDAMENTALS_FIXTURE = FUNDA;
    const res = await request(app).get(`/api/fundamentals/${encodeURIComponent('NASDAQ:AAPL')}`);
    expect(res.status).toBe(200);
    expect(res.body.symbol).toBe('NASDAQ:AAPL');
  });

  it('rejects an invalid symbol with 400', async () => {
    const res = await request(app).get('/api/fundamentals/not_a_symbol!!');
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/invalid symbol/i);
  });

  it('degrades to a structured error when the fixture is missing', async () => {
    process.env.TRADING_UI_FUNDAMENTALS_FIXTURE = path.join(FIXTURE_DIR, 'nope.json');
    const res = await request(app).get('/api/fundamentals/NASDAQ:AAPL');
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(false);
    expect(res.body.source).toBe('fixture');
    expect(res.body.data).toBeNull();
    expect(res.body.error).toMatch(/fixture not found/i);
  });

  it('degrades to a structured error when the scanner returns a bare null', async () => {
    // Write a fixture whose body is JSON `null` (what the scanner emits for an
    // unqualified symbol) to exercise the empty-payload path.
    const nullFixture = path.join(FIXTURE_DIR, 'fundamentals_null.json');
    const fs = await import('node:fs');
    fs.writeFileSync(nullFixture, 'null');
    try {
      process.env.TRADING_UI_FUNDAMENTALS_FIXTURE = nullFixture;
      const res = await request(app).get('/api/fundamentals/AAPL');
      expect(res.status).toBe(200);
      expect(res.body.ok).toBe(false);
      expect(res.body.error).toMatch(/EXCHANGE:TICKER/);
    } finally {
      fs.rmSync(nullFixture, { force: true });
    }
  });
});
