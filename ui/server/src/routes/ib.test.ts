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
    expect(res.body.orders).toHaveLength(5); // 2 standalone + a 3-leg AMD bracket
    const stop = res.body.orders.find((o: { symbol: string }) => o.symbol === 'MSFT');
    expect(stop.side).toBe('SELL');
    expect(stop.order_type).toBe('STP');
    expect(stop.stop_price).toBe(380.0);
    // Native-bracket linkage fields survive the snapshot round-trip so the UI
    // can collapse the three AMD legs into a single row.
    const amd = res.body.orders.filter((o: { symbol: string }) => o.symbol === 'AMD');
    expect(amd).toHaveLength(3);
    const amdParent = amd.find((o: { client_order_id: string | null }) => o.client_order_id);
    expect(amdParent.client_order_id).toBe('wl-amd-2026-06-15');
    const amdChildren = amd.filter(
      (o: { parent_id: string | null }) => o.parent_id === 'wl-amd-2026-06-15',
    );
    expect(amdChildren).toHaveLength(2);
    expect(res.body.trades).toHaveLength(2);
    const sell = res.body.trades.find((t: { symbol: string }) => t.symbol === 'TSLA');
    expect(sell.side).toBe('SELL');
    expect(sell.quantity).toBe(50);
    expect(sell.price).toBe(205.0);
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

describe('GET /api/ib/health', () => {
  it('reports ok when the fixture snapshot is healthy', async () => {
    process.env.TRADING_UI_IB_FIXTURE = SNAPSHOT;
    const res = await request(app).get('/api/ib/health');
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
    expect(res.body.authenticated).toBe(true);
    expect(res.body.reachable).toBe(true);
    expect(res.body.source).toBe('fixture');
    expect(typeof res.body.checked_at).toBe('string');
  });

  it('reports not-ok with a reason when the Gateway is unavailable', async () => {
    process.env.TRADING_UI_IB_FIXTURE = path.join(FIXTURE_DIR, 'does_not_exist.json');
    const res = await request(app).get('/api/ib/health');
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(false);
    expect(res.body.authenticated).toBe(false);
    expect(res.body.source).toBe('fixture');
    expect(typeof res.body.error).toBe('string');
    expect(res.body.error.length).toBeGreaterThan(0);
  });
});
