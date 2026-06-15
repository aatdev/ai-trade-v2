import { Router } from 'express';
import { fetchIbSnapshot } from '../lib/ibSnapshot';

/**
 * GET /api/ib — a live, read-only Interactive Brokers account + positions
 * snapshot. Always returns 200 with an IbSnapshot; connection problems surface
 * as `{ ok: false, error }` so the client renders a friendly state.
 */
export function ibRouter(projectRoot: string): Router {
  const r = Router();

  r.get('/ib', async (_req, res) => {
    const snapshot = await fetchIbSnapshot(projectRoot);
    res.json(snapshot);
  });

  return r;
}
