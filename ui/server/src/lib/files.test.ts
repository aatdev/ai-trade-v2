import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { clearListCache, findLatest, listDates, readJson, tailLines } from './files';
import { RE } from './mappers';

const FIXTURE = path.resolve(process.cwd(), 'test/fixture');

beforeEach(() => clearListCache());

describe('findLatest', () => {
  it('returns the newest timestamped file (lexicographic by stamp)', () => {
    const latest = findLatest(path.join(FIXTURE, 'market'), RE.breadth);
    expect(latest).toBeTruthy();
    expect(path.basename(latest!)).toBe('market_breadth_2026-06-11_133000.json');
  });

  it('does not confuse watchlist with watchlist_validation', () => {
    const wl = findLatest(path.join(FIXTURE, 'schedule'), RE.watchlist);
    expect(path.basename(wl!)).toBe('watchlist_2026-06-11.json');
  });

  it('filters by requested date and returns null when absent', () => {
    const d10 = findLatest(path.join(FIXTURE, 'schedule'), RE.exposureDecision, '2026-06-10');
    expect(path.basename(d10!)).toBe('exposure_decision_2026-06-10.json');
    const missing = findLatest(path.join(FIXTURE, 'schedule'), RE.exposureDecision, '1999-01-01');
    expect(missing).toBeNull();
  });

  it('returns null for an empty/missing directory without throwing', () => {
    expect(findLatest(path.join(FIXTURE, 'does-not-exist'), RE.breadth)).toBeNull();
  });
});

describe('listDates', () => {
  it('dedupes and sorts dates descending', () => {
    const dates = listDates([path.join(FIXTURE, 'schedule'), path.join(FIXTURE, 'market')]);
    expect(dates[0]).toBe('2026-06-11');
    expect(dates).toContain('2026-06-10');
    // descending
    expect([...dates].sort().reverse()).toEqual(dates);
  });
});

describe('readJson / tailLines', () => {
  let tmp: string;
  beforeEach(() => {
    tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ui-files-'));
  });
  afterEach(() => fs.rmSync(tmp, { recursive: true, force: true }));

  it('returns null on malformed JSON instead of throwing', () => {
    const f = path.join(tmp, 'bad.json');
    fs.writeFileSync(f, '{ not json');
    expect(readJson(f)).toBeNull();
    expect(readJson(null)).toBeNull();
  });

  it('tails the last N lines', () => {
    const f = path.join(tmp, 'log.txt');
    fs.writeFileSync(f, 'a\nb\nc\nd\n');
    expect(tailLines(f, 2)).toEqual(['c', 'd']);
    expect(tailLines(path.join(tmp, 'missing.txt'), 5)).toEqual([]);
  });
});
