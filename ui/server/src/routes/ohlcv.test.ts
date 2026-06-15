import path from 'node:path';
import request from 'supertest';
import { afterEach, describe, expect, it } from 'vitest';
import { createApp } from '../app';

const FIXTURE_DIR = path.resolve(process.cwd(), 'test/fixture');
const BARS = path.join(FIXTURE_DIR, 'ohlcv_bars.json');
const app = createApp({ dataDir: FIXTURE_DIR, projectRoot: path.resolve(process.cwd()) });

afterEach(() => {
  delete process.env.TRADING_UI_OHLCV_FIXTURE;
});

describe('GET /api/ohlcv/:symbol', () => {
  it('returns the recorded bars when TRADING_UI_OHLCV_FIXTURE is set', async () => {
    process.env.TRADING_UI_OHLCV_FIXTURE = BARS;
    const res = await request(app).get('/api/ohlcv/AAPL');
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
    expect(res.body.source).toBe('fixture');
    expect(res.body.symbol).toBe('AAPL');
    expect(res.body.resolved).toBe('NASDAQ:AAPL');
    expect(res.body.timeframe).toBe('D');
    expect(res.body.bars).toHaveLength(6);
    // Bars come back ascending by time with all OHLCV fields populated.
    expect(res.body.bars[0].time).toBe(1781011800);
    expect(res.body.bars[5].close).toBe(300.4);
    expect(res.body.bars[0].volume).toBe(70108847);
  });

  it('honours the tf query parameter', async () => {
    process.env.TRADING_UI_OHLCV_FIXTURE = BARS;
    const res = await request(app).get('/api/ohlcv/AAPL?tf=W');
    expect(res.status).toBe(200);
    expect(res.body.timeframe).toBe('W');
  });

  it('rejects an invalid symbol with 400', async () => {
    const res = await request(app).get('/api/ohlcv/not_a_symbol!!');
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/invalid symbol/i);
  });

  it('rejects an unknown timeframe with 400', async () => {
    const res = await request(app).get('/api/ohlcv/AAPL?tf=3h');
    expect(res.status).toBe(400);
    expect(res.body.error).toMatch(/invalid timeframe/i);
  });

  it('degrades to a structured error when the fixture is missing', async () => {
    process.env.TRADING_UI_OHLCV_FIXTURE = path.join(FIXTURE_DIR, 'does_not_exist.json');
    const res = await request(app).get('/api/ohlcv/AAPL');
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(false);
    expect(res.body.source).toBe('fixture');
    expect(res.body.error).toMatch(/fixture not found/i);
    expect(res.body.bars).toEqual([]);
  });
});
