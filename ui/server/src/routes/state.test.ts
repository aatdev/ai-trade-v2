import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import request from 'supertest';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
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

describe('GET /api/versions', () => {
  it('lists matching files for a kind, newest first', async () => {
    const res = await request(app).get('/api/versions?kind=exposure');
    expect(res.status).toBe(200);
    expect(res.body.kind).toBe('exposure');
    expect(res.body.versions).toEqual([
      'exposure_decision_2026-06-11.json',
      'exposure_decision_2026-06-10.json',
    ]);
  });

  it('orders timestamped snapshots newest first', async () => {
    const res = await request(app).get('/api/versions?kind=portfolio');
    expect(res.body.versions).toEqual([
      'portfolio_heat_2026-06-11_143000.json',
      'portfolio_heat_2026-06-11_120000.json',
    ]);
  });

  it('400s for an unknown kind', async () => {
    expect((await request(app).get('/api/versions?kind=bogus')).status).toBe(400);
    expect((await request(app).get('/api/versions')).status).toBe(400);
  });
});

describe('?source= pins a specific file version', () => {
  it('reads the requested historical exposure gate', async () => {
    const res = await request(app).get('/api/exposure?source=exposure_decision_2026-06-10.json');
    expect(res.body.gate.source).toBe('exposure_decision_2026-06-10.json');
    expect(res.body.gate.data.decision).toBe('allow');
  });

  it('reads an older portfolio heat snapshot', async () => {
    const res = await request(app).get('/api/portfolio?source=portfolio_heat_2026-06-11_120000.json');
    expect(res.body.source).toBe('portfolio_heat_2026-06-11_120000.json');
    expect(res.body.data.positions_count).toBe(0);
  });

  it('honors per-kind market source params', async () => {
    const res = await request(app).get('/api/market?breadthSource=market_breadth_2026-06-11_120000.json');
    expect(res.body.breadth.source).toBe('market_breadth_2026-06-11_120000.json');
    expect(res.body.breadth.data.composite_score).toBe(40);
  });

  it('falls back to the latest file when source is invalid or unknown', async () => {
    // path-traversal / wrong-pattern names are rejected, then resolved to latest
    const evil = await request(app).get('/api/portfolio?source=../secrets.json');
    expect(evil.body.source).toBe('portfolio_heat_2026-06-11_143000.json');
    expect(evil.body.data.positions_count).toBe(1);
    const missing = await request(app).get('/api/portfolio?source=portfolio_heat_1999-01-01_000000.json');
    expect(missing.body.source).toBe('portfolio_heat_2026-06-11_143000.json');
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

describe('GET /api/memory', () => {
  it('returns full theses with a computed summary', async () => {
    const res = await request(app).get('/api/memory');
    expect(res.status).toBe(200);
    const aapl = res.body.theses.find((t: { ticker: string }) => t.ticker === 'AAPL');
    expect(aapl).toBeTruthy();
    expect(aapl.review_due).toBe(true);
    expect(typeof aapl.thesis_statement).toBe('string');
    // detail carries entry/exit levels parsed from the thesis yaml
    expect(aapl.entry.target_price).toBe(315);
    expect(aapl.exit.stop_loss).toBe(305);
    expect(res.body.summary.byStatus.IDEA).toBeGreaterThanOrEqual(1);
    expect(res.body.summary.reviewDue).toBeGreaterThanOrEqual(1);
    expect(typeof res.body.today).toBe('string');
  });
});

describe('GET /api/skill-doc/:skill', () => {
  const docsApp = createApp({ dataDir: FIXTURE, projectRoot: FIXTURE });

  it('returns SKILL.md plus reference docs', async () => {
    const res = await request(docsApp).get('/api/skill-doc/demo-skill');
    expect(res.status).toBe(200);
    expect(res.body.skill).toBe('demo-skill');
    expect(res.body.docs[0].name).toBe('SKILL.md');
    expect(res.body.docs[0].content).toContain('Demo Skill');
    expect(res.body.docs.some((d: { name: string }) => d.name === 'references/lifecycle.md')).toBe(true);
  });

  it('404s for an unknown skill and 400s for an invalid name', async () => {
    expect((await request(docsApp).get('/api/skill-doc/nope-not-here')).status).toBe(404);
    // dots/slashes are outside the slug charset → rejected before any fs access
    expect((await request(docsApp).get('/api/skill-doc/foo.bar')).status).toBe(400);
  });
});

describe('GET /api/analysis/tickers', () => {
  it('lists tickers that have saved analysis with their latest date', async () => {
    const res = await request(app).get('/api/analysis/tickers');
    expect(res.status).toBe(200);
    expect(res.body.tickers.AAPL).toEqual({
      latest: '2026-06-11',
      count: 1,
      dates: ['2026-06-11'],
    });
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

describe('GET /api/autopilot', () => {
  // Logs live under a gitignored logs/ dir, so seed a throwaway data dir
  // rather than committing .log fixtures.
  let tmp: string;
  let tApp: ReturnType<typeof createApp>;
  beforeEach(() => {
    tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'state-autopilot-'));
    fs.mkdirSync(path.join(tmp, 'logs'));
    fs.writeFileSync(
      path.join(tmp, 'logs', 'trading_schedule.log'),
      '[2026-06-11 22:28:05] INFO    ===== RUN END slot=evening-prep rc=0 elapsed=784.8s =====\n',
    );
    fs.writeFileSync(
      path.join(tmp, 'logs', 'autopilot_cron.log'),
      '[2026-06-12 10:30:00] DECISION: none — no-op run complete\n',
    );
    tApp = createApp({ dataDir: tmp, projectRoot: path.resolve(process.cwd()) });
  });
  afterEach(() => fs.rmSync(tmp, { recursive: true, force: true }));

  it('returns both the per-slot schedule log and the autopilot loop log', async () => {
    const res = await request(tApp).get('/api/autopilot');
    expect(res.status).toBe(200);
    // Per-slot run log (only written when a slot executes).
    expect(res.body.logTail).toContain(
      '[2026-06-11 22:28:05] INFO    ===== RUN END slot=evening-prep rc=0 elapsed=784.8s =====',
    );
    // Autopilot cron loop log — the freshest stream between slot runs.
    expect(res.body.cronLogTail.join('\n')).toContain('no-op run complete');
  });
});

describe('PUT /api/profile', () => {
  // The fixture profile is read-only/shared, so PUT runs against a throwaway
  // data dir seeded with a copy of it.
  let tmp: string;
  let tApp: ReturnType<typeof createApp>;
  beforeEach(() => {
    tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'state-profile-'));
    fs.copyFileSync(path.join(FIXTURE, 'trading_profile.json'), path.join(tmp, 'trading_profile.json'));
    tApp = createApp({ dataDir: tmp, projectRoot: path.resolve(process.cwd()) });
  });
  afterEach(() => fs.rmSync(tmp, { recursive: true, force: true }));

  it('writes a partial patch, reports recalc impact, and persists to disk', async () => {
    const res = await request(tApp).put('/api/profile').send({ risk_pct: 2, atr_multiplier: 2.5 });
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
    expect(res.body.profile.risk_pct).toBe(2);
    expect(res.body.profile.account_size).toBe(150000); // merged/preserved
    expect(res.body.changed.sort()).toEqual(['atr_multiplier', 'risk_pct']);
    expect(res.body.recalcAffected.sort()).toEqual(['atr_multiplier', 'risk_pct']);
    expect(res.body.screenOnlyAffected).toEqual([]);

    // Re-read through the API and straight off disk.
    const back = await request(tApp).get('/api/profile');
    expect(back.body.risk_pct).toBe(2);
    const onDisk = JSON.parse(fs.readFileSync(path.join(tmp, 'trading_profile.json'), 'utf8'));
    expect(onDisk.risk_pct).toBe(2);
    expect(onDisk.atr_multiplier).toBe(2.5);
  });

  it('flags a sector-RS change as screen-only (needs re-screen, not re-plan)', async () => {
    const res = await request(tApp).put('/api/profile').send({ sector_rs_threshold: 8 });
    expect(res.body.recalcAffected).toEqual(['sector_rs_threshold']);
    expect(res.body.screenOnlyAffected).toEqual(['sector_rs_threshold']);
  });

  it('400s an out-of-range value without touching the file', async () => {
    const res = await request(tApp).put('/api/profile').send({ risk_pct: 999 });
    expect(res.status).toBe(400);
    expect(res.body.ok).toBe(false);
    const onDisk = JSON.parse(fs.readFileSync(path.join(tmp, 'trading_profile.json'), 'utf8'));
    expect(onDisk.risk_pct).toBe(1); // unchanged
  });
});
