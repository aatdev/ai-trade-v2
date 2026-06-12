import fs from 'node:fs';
import path from 'node:path';
import express, { type Express } from 'express';
import { JobManager } from './lib/jobs';
import { actionsRouter } from './routes/actions';
import { stateRouter } from './routes/state';
import { tickerRouter } from './routes/ticker';
import { watchlistRouter } from './routes/watchlist';

export interface AppOptions {
  dataDir: string;
  projectRoot: string;
  /** Optional pre-built job manager (tests inject a stub-friendly instance). */
  jobs?: JobManager;
  /** Serve the built client from this directory (prod single-port mode). */
  clientDist?: string;
}

export function createApp(opts: AppOptions): Express {
  const app = express();
  app.use(express.json());

  const jobs = opts.jobs ?? new JobManager();

  app.get('/api/health', (_req, res) => res.json({ ok: true }));
  app.use('/api', stateRouter(opts.dataDir));
  app.use('/api', tickerRouter(opts.dataDir));
  app.use('/api', watchlistRouter(opts.dataDir));
  app.use('/api', actionsRouter(opts.projectRoot, jobs));

  // Production: serve the built SPA and fall back to index.html for routes.
  const dist = opts.clientDist ?? path.resolve(__dirname, '..', '..', 'client', 'dist');
  if (fs.existsSync(path.join(dist, 'index.html'))) {
    app.use(express.static(dist));
    app.get(/^(?!\/api).*/, (_req, res) => res.sendFile(path.join(dist, 'index.html')));
  }

  return app;
}
