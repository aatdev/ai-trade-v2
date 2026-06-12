import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import request from 'supertest';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { AnalysisSignal, Watchlist, WatchlistCandidate } from '@shared/types';
import { createApp } from '../app';
import { clearListCache } from './files';
import { applyReconcile, reconcile } from './reconcile';
import { parseSignalBlocks, parseSignalLevels } from './signals';

function candidate(over: Partial<WatchlistCandidate>): WatchlistCandidate {
  return {
    ticker: 'AOS',
    side: 'short',
    setup: 'Stage 4 (grade A)',
    pivot: 58.66,
    worst_entry: 57.49,
    stop: 59.47,
    target: 57.04,
    shares: 639,
    risk_dollars: 517.59,
    score: 80.3,
    plan_type: 'stage4_breakdown',
    validation_note: null,
    validated: null,
    ...over,
  };
}

function watchlist(candidates: WatchlistCandidate[]): Watchlist {
  return {
    workflow: 'swing-opportunity-daily',
    date: '2026-06-11',
    exposure_decision: 'restrict',
    candidates,
    rejected_by_validation: [],
    notes: null,
    source_plan: 'trading-data/screeners/swing_short_screener_2026-06-11.json',
  };
}

const longSignal: AnalysisSignal = {
  ticker: 'AOS',
  date: '2026-06-12',
  direction: 'long',
  trigger: 60,
  stop: 56,
  t1: 64,
  t2: 70,
  t3: 78,
  entryLow: 58,
  entryHigh: 60.5,
};

describe('parseSignalLevels', () => {
  it('extracts direction + trigger/stop/T1-T3 + entry range', () => {
    const md = [
      '# Trading Signals Journal',
      '',
      '---',
      '',
      '## 2026-06-12 — AOS — 🟢 BUY (reversal)',
      '',
      '- **Trigger для Long:** close 1D > $60.00',
      '- **Entry (Long):** $58.00–$60.50',
      '- **Stop:** $56.00',
      '- **T1 / T2 / T3:** $64.00 / $70.00 / $78.00',
      '- **Альтернатива (Short):** close < $55 → stop $58, T1 $50',
      '',
    ].join('\n');
    const block = parseSignalBlocks(md)[0];
    const sig = parseSignalLevels(block)!;
    expect(sig.direction).toBe('long');
    expect(sig.trigger).toBe(60);
    expect(sig.stop).toBe(56); // not the alternative-scenario $58
    expect([sig.t1, sig.t2, sig.t3]).toEqual([64, 70, 78]);
    expect([sig.entryLow, sig.entryHigh]).toEqual([58, 60.5]);
  });
});

describe('reconcile', () => {
  const profile = { account_size: 150000, risk_pct: 1 };

  it('excludes the candidate on a direction flip (no silent side change)', () => {
    const wl = watchlist([candidate({ side: 'short' })]);
    const r = reconcile(wl, 'AOS', longSignal, profile);
    expect(r.change).toBe('direction-flip');
    // proposed is an exclusion record, not a flipped active candidate
    expect(r.proposed!.side).toBe('short');
    expect(r.proposed!.source).toBe('analysis-excluded');
    expect(r.proposed!.validated).toBe(false);
    expect(r.proposed!.validation_note).toContain('direction-flip');
    // original screener values preserved
    expect(r.proposed!.screener_origin?.side).toBe('short');
    expect(r.proposed!.screener_origin?.pivot).toBe(58.66);
  });

  it('re-sizes level updates from the profile budget, not inherited risk_dollars', () => {
    const wl = watchlist([candidate({ side: 'long', pivot: 59, stop: 56.5, target: 63 })]);
    const r = reconcile(wl, 'AOS', longSignal, profile);
    expect(r.change).toBe('levels-updated');
    // budget = 150000 × 1% = 1500; shares = 1500 / |60−56| = 375
    // (NOT the old candidate's 517.59 / 4 ≈ 129)
    expect(r.proposed!.shares).toBe(375);
    expect(r.proposed!.risk_dollars).toBe(1500);
    // worst_entry from the analysis Entry range, not pivot itself
    expect(r.proposed!.worst_entry).toBe(60.5);
  });

  it('caps shares at max_position_pct on a tight stop', () => {
    const wl = watchlist([]);
    const tightStop: AnalysisSignal = { ...longSignal, trigger: 60, stop: 59.9 };
    const r = reconcile(wl, 'AOS', tightStop, { ...profile, max_position_pct: 25 });
    // budget shares = 1500 / 0.1 = 15000; cap = 150000×25% / 60 = 625
    expect(r.proposed!.shares).toBe(625);
    expect(r.proposed!.risk_dollars).toBe(62.5);
  });

  it('falls back to chase % for worst_entry without an Entry range', () => {
    const wl = watchlist([]);
    const noRange: AnalysisSignal = { ...longSignal, entryLow: null, entryHigh: null };
    const r = reconcile(wl, 'AOS', noRange, profile);
    expect(r.proposed!.worst_entry).toBe(61.2); // 60 × 1.02
  });

  it('reports unchanged when side and levels already match', () => {
    const wl = watchlist([candidate({ side: 'long', pivot: 60, stop: 56, target: 64 })]);
    const r = reconcile(wl, 'AOS', longSignal, profile);
    expect(r.change).toBe('unchanged');
  });

  it('treats an unknown ticker as new and sizes from the profile', () => {
    const wl = watchlist([]);
    const r = reconcile(wl, 'AOS', longSignal, profile);
    expect(r.change).toBe('new');
    // risk = 150000 * 1% = 1500; shares = 1500 / 4 = 375
    expect(r.proposed!.shares).toBe(375);
    expect(r.proposed!.screener_origin).toBeNull();
  });

  it('returns no-analysis when there is no signal', () => {
    const wl = watchlist([candidate({})]);
    const r = reconcile(wl, 'AOS', null, profile);
    expect(r.change).toBe('no-analysis');
    expect(r.proposed).toBeNull();
  });

  it('preserves thesis_id across a level update', () => {
    const wl = watchlist([
      candidate({ side: 'long', pivot: 59, stop: 56.5, thesis_id: 'th_aos_pvt_20260611_ab12' }),
    ]);
    const r = reconcile(wl, 'AOS', longSignal, profile);
    expect(r.proposed!.thesis_id).toBe('th_aos_pvt_20260611_ab12');
  });

  it('applyReconcile parks a direction-flip under rejected_by_validation', () => {
    const wl = watchlist([candidate({ side: 'short' })]);
    const r = reconcile(wl, 'AOS', longSignal, profile);
    const next = applyReconcile(wl, r.proposed!, '2026-06-12');
    expect(next.candidates).toHaveLength(0);
    expect(next.rejected_by_validation.map((c) => c.ticker)).toEqual(['AOS']);
    expect(next.rejected_by_validation[0].source).toBe('analysis-excluded');
  });

  it('applyReconcile re-promotes a same-direction reject (analysis-authoritative)', () => {
    const wl = watchlist([]);
    wl.rejected_by_validation = [candidate({ ticker: 'AOS', side: 'long', validated: false })];
    const r = reconcile(wl, 'AOS', longSignal, profile);
    expect(r.change).toBe('levels-updated');
    const next = applyReconcile(wl, r.proposed!, '2026-06-12');
    expect(next.candidates.map((c) => c.ticker)).toEqual(['AOS']);
    expect(next.rejected_by_validation).toHaveLength(0);
  });
});

describe('parseSignalLevels HOLD guard', () => {
  it('refuses to arm levels from a 🟡 HOLD block even with a Trigger line', () => {
    const md = [
      '# Trading Signals Journal',
      '',
      '---',
      '',
      '## 2026-06-12 — ALLE — 🟡 HOLD (отскок к сопротивлению)',
      '',
      '- **Trigger для Long:** close 1D > $135.50',
      '- **Stop:** $129.40',
      '- **T1 / T2 / T3:** $138.50 / $144.50 / $148.80',
      '',
    ].join('\n');
    const block = parseSignalBlocks(md)[0];
    expect(parseSignalLevels(block)).toBeNull();
  });
});

/* ---------------- route (writes to a temp data dir) ---------------- */

function tmpDir(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ui-reconcile-'));
  fs.mkdirSync(path.join(dir, 'schedule'), { recursive: true });
  fs.mkdirSync(path.join(dir, 'analysis'), { recursive: true });
  fs.writeFileSync(
    path.join(dir, 'schedule', 'watchlist_2026-06-11.json'),
    JSON.stringify(watchlist([candidate({ side: 'short' })]), null, 2),
  );
  fs.writeFileSync(
    path.join(dir, 'analysis', 'signals.md'),
    [
      '# Trading Signals Journal',
      '',
      '---',
      '',
      '## 2026-06-12 — AOS — 🟢 BUY',
      '',
      '- **Trigger для Long:** close 1D > $60.00',
      '- **Entry (Long):** $58.00–$60.50',
      '- **Stop:** $56.00',
      '- **T1 / T2 / T3:** $64.00 / $70.00 / $78.00',
      '',
    ].join('\n'),
  );
  fs.writeFileSync(
    path.join(dir, 'trading_profile.json'),
    JSON.stringify({ account_size: 150000, risk_pct: 1 }),
  );
  return dir;
}

describe('watchlist reconcile routes', () => {
  let dir: string;
  beforeEach(() => {
    clearListCache();
    dir = tmpDir();
  });
  afterEach(() => fs.rmSync(dir, { recursive: true, force: true }));

  it('GET previews a direction flip; POST excludes the candidate on disk', async () => {
    const app = createApp({ dataDir: dir, projectRoot: dir });

    const preview = await request(app).get('/api/watchlist/reconcile/AOS');
    expect(preview.status).toBe(200);
    expect(preview.body.change).toBe('direction-flip');
    expect(preview.body.proposed.source).toBe('analysis-excluded');

    const apply = await request(app).post('/api/watchlist/reconcile/AOS');
    expect(apply.body.applied).toBe(true);

    // persisted to disk: moved out of candidates into rejected_by_validation
    const onDisk = JSON.parse(
      fs.readFileSync(path.join(dir, 'schedule', 'watchlist_2026-06-11.json'), 'utf8'),
    ) as Watchlist;
    expect(onDisk.candidates.find((c) => c.ticker === 'AOS')).toBeUndefined();
    const aos = onDisk.rejected_by_validation.find((c) => c.ticker === 'AOS')!;
    expect(aos.source).toBe('analysis-excluded');
    expect(aos.side).toBe('short'); // original side kept for the audit trail

    // and the read API reflects it
    const wl = await request(app).get('/api/watchlist');
    expect(
      wl.body.data.candidates.find((c: { ticker: string }) => c.ticker === 'AOS'),
    ).toBeUndefined();
  });

  it('rejects an invalid ticker', async () => {
    const app = createApp({ dataDir: dir, projectRoot: dir });
    expect((await request(app).get('/api/watchlist/reconcile/AB$')).status).toBe(400);
  });
});
