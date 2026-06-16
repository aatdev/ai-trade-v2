import { Router } from 'express';
import { fetchIbHealth } from '../lib/ibHealth';
import { fetchIbSnapshot } from '../lib/ibSnapshot';

/**
 * GET /api/ib — a live, read-only Interactive Brokers account + positions
 * snapshot. Always returns 200 with an IbSnapshot; connection problems surface
 * as `{ ok: false, error }` so the client renders a friendly state.
 *
 * GET /api/ib/health — a cheap Gateway liveness probe meant to be polled on an
 * interval (drives the red "Счёт IB" tab indicator). Also always 200.
 */
export function ibRouter(projectRoot: string): Router {
  const r = Router();

  r.get('/ib/health', async (_req, res) => {
    const status = await fetchIbHealth(projectRoot);
    res.json(status);
  });

  r.get('/ib', async (_req, res) => {
    const snapshot = await fetchIbSnapshot(projectRoot);
    res.json(snapshot);
  });

  return r;
}
