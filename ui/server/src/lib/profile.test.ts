import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import type { TradingProfile } from '@shared/types';
import {
  buildSaveResponse,
  diffChangedKeys,
  readProfileFile,
  resolveProfilePath,
  validateProfilePatch,
  writeProfileFile,
} from './profile';

const BASE: TradingProfile = {
  account_size: 150000,
  risk_pct: 1,
  max_position_pct: 25,
  max_sector_pct: 30,
  max_portfolio_heat_pct: 6,
  max_positions: 6,
  target_r_multiple: 2,
  earnings_gate_days: 10,
  time_stop_trading_days: 15,
  atr_multiplier: 2,
  fundamental_gate: 1,
  sector_rs_gate: 1,
  sector_rs_threshold: 5,
};

describe('validateProfilePatch', () => {
  it('merges a partial patch over the existing profile', () => {
    const out = validateProfilePatch({ risk_pct: 2 }, BASE);
    expect('profile' in out).toBe(true);
    if ('profile' in out) {
      expect(out.profile.risk_pct).toBe(2);
      expect(out.profile.account_size).toBe(150000); // preserved
      expect(out.profile.max_positions).toBe(6); // preserved
    }
  });

  it('preserves unknown legacy keys already on disk', () => {
    const out = validateProfilePatch({ risk_pct: 2 }, { ...BASE, stop_buffer_pct: 0.5 });
    if ('profile' in out) expect(out.profile.stop_buffer_pct).toBe(0.5);
    else throw new Error('expected profile');
  });

  it('rejects out-of-range and non-integer values', () => {
    expect('error' in validateProfilePatch({ risk_pct: 999 }, BASE)).toBe(true);
    expect('error' in validateProfilePatch({ risk_pct: 0 }, BASE)).toBe(true);
    expect('error' in validateProfilePatch({ max_positions: 2.5 }, BASE)).toBe(true);
    expect('error' in validateProfilePatch({ fundamental_gate: 2 }, BASE)).toBe(true);
    expect('error' in validateProfilePatch({ atr_multiplier: 0 }, BASE)).toBe(true);
  });

  it('rejects non-number values and non-object bodies', () => {
    expect('error' in validateProfilePatch({ risk_pct: 'high' }, BASE)).toBe(true);
    expect('error' in validateProfilePatch({ risk_pct: Infinity }, BASE)).toBe(true);
    expect('error' in validateProfilePatch(null, BASE)).toBe(true);
    expect('error' in validateProfilePatch([1, 2], BASE)).toBe(true);
  });

  it('requires a valid account_size in the merged result', () => {
    // No existing profile and a patch without account_size → invalid.
    expect('error' in validateProfilePatch({ risk_pct: 2 }, null)).toBe(true);
    expect('error' in validateProfilePatch({ account_size: 0 }, null)).toBe(true);
    expect('profile' in validateProfilePatch({ ...BASE }, null)).toBe(true);
  });
});

describe('diffChangedKeys + buildSaveResponse', () => {
  it('flags only changed keys', () => {
    expect(diffChangedKeys(BASE, { ...BASE, risk_pct: 2 })).toEqual(['risk_pct']);
    expect(diffChangedKeys(BASE, { ...BASE })).toEqual([]);
  });

  it('splits changed → recalc-affecting → screen-only', () => {
    const next = { ...BASE, risk_pct: 2, max_positions: 8, sector_rs_threshold: 8 };
    const r = buildSaveResponse(BASE, next);
    expect(r.ok).toBe(true);
    expect(r.changed?.sort()).toEqual(['max_positions', 'risk_pct', 'sector_rs_threshold']);
    // max_positions is a slot LIMIT — not a re-plan input.
    expect(r.recalcAffected?.sort()).toEqual(['risk_pct', 'sector_rs_threshold']);
    // sector_rs_threshold is applied at screen time → needs a re-screen, not a re-plan.
    expect(r.screenOnlyAffected).toEqual(['sector_rs_threshold']);
  });

  it('treats every key as changed when there was no prior profile', () => {
    expect(diffChangedKeys(null, { ...BASE }).length).toBe(Object.keys(BASE).length);
  });
});

describe('read/write round-trip', () => {
  let tmp: string | null = null;
  afterEach(() => {
    if (tmp) fs.rmSync(tmp, { recursive: true, force: true });
    tmp = null;
  });

  it('writes the data-dir copy and reads it back', () => {
    tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'profile-'));
    const dest = writeProfileFile(tmp, BASE);
    expect(dest).toBe(path.join(tmp, 'trading_profile.json'));
    expect(resolveProfilePath(tmp)).toBe(dest);
    const back = readProfileFile(tmp);
    expect(back?.account_size).toBe(150000);
    expect(back?.sector_rs_threshold).toBe(5);
  });

  it('returns null when no profile exists', () => {
    tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'profile-'));
    expect(readProfileFile(tmp)).toBeNull();
  });
});
