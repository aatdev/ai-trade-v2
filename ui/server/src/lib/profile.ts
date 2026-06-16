import fs from 'node:fs';
import path from 'node:path';
import type { SaveProfileResponse, TradingProfile } from '@shared/types';

/** Per-field validation + recalc-impact metadata for trading_profile.json. */
export interface ProfileFieldSpec {
  min: number;
  max: number;
  integer?: boolean;
  /**
   * Editing this changes watchlist sizing/levels or non-active (IDEA/ENTRY_READY)
   * thesis levels, so a re-plan recalc is warranted. (max_positions is a portfolio
   * slot LIMIT consumed only by the heat report / new-entry gating — the planner
   * does not re-size existing candidates from it — so it is intentionally absent.)
   */
  affectsRecalc?: boolean;
  /**
   * Applied at SCREEN time (screen_vcp.py), not by the planner — a re-plan over
   * the frozen latest screener cannot reflect a change here; it needs a full
   * evening-prep re-screen.
   */
  screenOnly?: boolean;
}

/**
 * The fields the UI exposes, with ranges that mirror the consuming scripts'
 * argparse bounds so a server 400 equals what the script would reject. Unknown
 * keys already on disk (e.g. the planner's optional stop_buffer_pct) are
 * preserved untouched by a write; they just are not range-checked here.
 */
export const PROFILE_SPEC: Record<string, ProfileFieldSpec> = {
  account_size: { min: 1, max: 1_000_000_000, affectsRecalc: true },
  risk_pct: { min: 0.01, max: 100, affectsRecalc: true },
  max_position_pct: { min: 0.1, max: 100, affectsRecalc: true },
  max_sector_pct: { min: 0.1, max: 100, affectsRecalc: true },
  max_portfolio_heat_pct: { min: 0.1, max: 100, affectsRecalc: true },
  max_positions: { min: 1, max: 100, integer: true },
  target_r_multiple: { min: 0.1, max: 20, affectsRecalc: true },
  earnings_gate_days: { min: 0, max: 60, integer: true, affectsRecalc: true },
  time_stop_trading_days: { min: 0, max: 365, integer: true, affectsRecalc: true },
  atr_multiplier: { min: 0.1, max: 20, affectsRecalc: true },
  fundamental_gate: { min: 0, max: 1, integer: true, affectsRecalc: true },
  sector_rs_gate: { min: 0, max: 1, integer: true, affectsRecalc: true, screenOnly: true },
  sector_rs_threshold: { min: 0, max: 100, affectsRecalc: true, screenOnly: true },
};

export const PROFILE_KEYS = Object.keys(PROFILE_SPEC);

/**
 * Resolve the canonical trading_profile.json path. The skill scripts read
 * `$TRADING_DATE_DIR/trading_profile.json` (i.e. the data dir), so that is the
 * authoritative file we read AND write — keeping the UI in lock-step with what
 * a recalc/scheduler run actually uses. A legacy repo-root copy (one level up)
 * is honored for READ only when the data-dir file is absent.
 */
export function resolveProfilePath(dataDir: string): string {
  const inData = path.join(dataDir, 'trading_profile.json');
  if (fs.existsSync(inData)) return inData;
  const legacyRoot = path.join(dataDir, '..', 'trading_profile.json');
  if (fs.existsSync(legacyRoot)) return legacyRoot;
  return inData; // create here when neither exists
}

export function readProfileFile(dataDir: string): TradingProfile | null {
  const p = resolveProfilePath(dataDir);
  try {
    const raw = JSON.parse(fs.readFileSync(p, 'utf8')) as unknown;
    if (raw && typeof raw === 'object' && !Array.isArray(raw)) return raw as TradingProfile;
    return null;
  } catch {
    return null;
  }
}

/**
 * Validate a PUT body against the spec, MERGED over the existing on-disk profile
 * (so a partial submit or unknown legacy keys survive). Every provided value must
 * be a finite number; known keys are range/integer-checked; the merged result
 * must carry a valid account_size (the planner hard-requires it). Pure + exported
 * for unit tests.
 */
export function validateProfilePatch(
  body: unknown,
  existing: TradingProfile | null,
): { profile: TradingProfile } | { error: string } {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { error: 'body must be a JSON object' };
  }
  const merged: Record<string, number> = {};
  if (existing) {
    for (const [k, v] of Object.entries(existing)) {
      if (typeof v === 'number' && Number.isFinite(v)) merged[k] = v;
    }
  }
  for (const [k, v] of Object.entries(body as Record<string, unknown>)) {
    if (typeof v !== 'number' || !Number.isFinite(v)) {
      return { error: `${k} must be a finite number` };
    }
    const spec = PROFILE_SPEC[k];
    if (spec) {
      if (v < spec.min || v > spec.max) {
        return { error: `${k} must be in [${spec.min}, ${spec.max}]` };
      }
      if (spec.integer && !Number.isInteger(v)) {
        return { error: `${k} must be an integer` };
      }
    }
    merged[k] = v;
  }
  const acct = merged.account_size;
  if (typeof acct !== 'number' || !Number.isFinite(acct) || acct < 1) {
    return { error: 'account_size is required and must be ≥ 1' };
  }
  return { profile: merged as TradingProfile };
}

/** Crash-safe JSON write (tmp + rename), mirroring the scripts' _atomic_write_json. */
export function writeProfileFile(dataDir: string, profile: TradingProfile): string {
  const dest = resolveProfilePath(dataDir);
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  const tmp = path.join(path.dirname(dest), `.trading_profile.json.${process.pid}.tmp`);
  fs.writeFileSync(tmp, `${JSON.stringify(profile, null, 2)}\n`, 'utf8');
  fs.renameSync(tmp, dest);
  return dest;
}

/** Keys whose value differs between prev and next (number-compared). */
export function diffChangedKeys(
  prev: TradingProfile | null,
  next: TradingProfile,
): string[] {
  const changed: string[] = [];
  for (const k of Object.keys(next)) {
    if (!prev || prev[k] !== next[k]) changed.push(k);
  }
  return changed;
}

/** Build the PUT response: the saved profile + which changed / need a recalc. */
export function buildSaveResponse(
  prev: TradingProfile | null,
  next: TradingProfile,
): SaveProfileResponse {
  const changed = diffChangedKeys(prev, next);
  const recalcAffected = changed.filter((k) => PROFILE_SPEC[k]?.affectsRecalc);
  const screenOnlyAffected = recalcAffected.filter((k) => PROFILE_SPEC[k]?.screenOnly);
  return { ok: true, profile: next, changed, recalcAffected, screenOnlyAffected };
}
