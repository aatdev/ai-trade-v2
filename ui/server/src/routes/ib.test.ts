import path from 'node:path';
import request from 'supertest';
import { afterEach, describe, expect, it } from 'vitest';
import { createApp } from '../app';

const FIXTURE_DIR = path.resolve(process.cwd(), 'test/fixture');
const SNAPSHOT = path.join(FIXTURE_DIR, 'ib_snapshot.json');
const app = createApp({ dataDir: FIXTURE_DIR, projectRoot: path.resolve(process.cwd()) });

afterEach(() => {
  delete process.env.TRADING_UI_IB_FIXTURE;
});

describe('GET /api/ib', () => {
  it('returns the recorded snapshot when TRADING_UI_IB_FIXTURE is set', async () => {
    process.env.TRADING_UI_IB_FIXTURE = SNAPSHOT;
    const res = await request(app).get('/api/ib');
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
    expect(res.body.source).toBe('fixture');
    expect(res.body.account_id).toBe('DU1234567');
    expect(res.body.summary.net_liquidation).toBe(152340.55);
    expect(res.body.positions).toHaveLength(2);
    const tsla = res.body.positions.find((p: { symbol: string }) => p.symbol === 'TSLA');
    expect(tsla.side).toBe('short');
    expect(tsla.position).toBe(-50);
  });

  it('degrades to a structured error when the fixture is missing', async () => {
    process.env.TRADING_UI_IB_FIXTURE = path.join(FIXTURE_DIR, 'does_not_exist.json');
    const res = await request(app).get('/api/ib');
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(false);
    expect(res.body.source).toBe('fixture');
    expect(res.body.error).toMatch(/fixture not found/i);
    expect(res.body.positions).toEqual([]);
  });
});
