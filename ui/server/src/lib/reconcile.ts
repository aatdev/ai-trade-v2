import type {
  AnalysisSignal,
  ReconcileChange,
  ReconcileResult,
  ScreenerOrigin,
  Watchlist,
  WatchlistCandidate,
} from '@shared/types';

const EPS = 0.005; // price-equality tolerance
// Entry chase band when the analysis block has no explicit Entry range —
// mirrors DEFAULT_CHASE_PCT in scripts/lib/trading_signals.py.
const CHASE_PCT = 2;
const DEFAULT_MAX_POSITION_PCT = 25;

const round2 = (v: number): number => Math.round(v * 100) / 100;

export interface SizingProfile {
  account_size: number;
  risk_pct: number;
  max_position_pct?: number | null;
}

function near(a: number | null, b: number | null): boolean {
  if (a == null || b == null) return a === b;
  return Math.abs(a - b) <= EPS;
}

function findCandidate(wl: Watchlist | null, ticker: string): WatchlistCandidate | null {
  if (!wl) return null;
  const T = ticker.toUpperCase();
  return (
    wl.candidates.find((c) => c.ticker.toUpperCase() === T) ??
    wl.rejected_by_validation.find((c) => c.ticker.toUpperCase() === T) ??
    null
  );
}

function originSnapshot(c: WatchlistCandidate, sourcePlan: string | null): ScreenerOrigin {
  return {
    side: c.side,
    pivot: c.pivot,
    stop: c.stop,
    target: c.target,
    shares: c.shares,
    score: c.score,
    source_plan: sourcePlan,
  };
}

/**
 * Risk-budget sizing for an analysis-updated candidate: shares from the
 * profile risk % (account × risk% / |pivot − stop|), capped so the position
 * never exceeds max_position_pct of the account. Never inherits the previous
 * candidate's risk_dollars — that number is the *achieved post-cap* risk of
 * the old geometry, not a budget (a capped 0.1%-risk short once resized a
 * flipped long to 1/9 of the intended risk).
 */
function sizeFromProfile(
  profile: SizingProfile | null,
  pivot: number,
  stop: number,
): { shares: number; risk_dollars: number } | null {
  if (!profile || profile.account_size <= 0 || profile.risk_pct <= 0 || pivot <= 0) return null;
  const dist = Math.abs(pivot - stop);
  if (dist <= 0) return null;
  const budget = profile.account_size * (profile.risk_pct / 100);
  const capPct = profile.max_position_pct ?? DEFAULT_MAX_POSITION_PCT;
  const cap = Math.floor((profile.account_size * capPct) / 100 / pivot);
  const shares = Math.min(Math.floor(budget / dist), cap);
  if (shares <= 0) return null;
  return { shares, risk_dollars: round2(shares * dist) };
}

/**
 * Decide how the analysis signal should change the watchlist candidate.
 *
 * Policy (unified with the scheduler's `_auto_analyze_reconcile`):
 *   - same direction → analysis is authoritative for LEVELS; shares re-sized
 *     from the profile risk budget with the max-position cap;
 *   - DIRECTION FLIP → the candidate is EXCLUDED (moved aside into
 *     `rejected_by_validation`), never silently converted: a screener short
 *     re-read as a long needs a full re-plan, not a sign change.
 * The original screener values are preserved under `screener_origin`.
 */
export function reconcile(
  wl: Watchlist | null,
  ticker: string,
  analysis: AnalysisSignal | null,
  profile: SizingProfile | null,
): ReconcileResult {
  const T = ticker.toUpperCase();
  const current = findCandidate(wl, T);

  if (!analysis) {
    return { ticker: T, change: 'no-analysis', analysis: null, current, proposed: null };
  }

  const side = analysis.direction;

  // Preserve the *original* screener snapshot across repeated reconciles.
  const screener_origin =
    current?.screener_origin ??
    (current ? originSnapshot(current, wl?.source_plan ?? null) : null);

  if (current && (current.side || '').toLowerCase() !== side) {
    const excluded: WatchlistCandidate = {
      ...current,
      validation_note:
        `Excluded by analysis direction-flip: signal=${side}, ` +
        `screener=${(current.side || '?').toLowerCase()} (${analysis.date})`,
      validated: false,
      source: 'analysis-excluded',
      screener_origin,
    };
    return { ticker: T, change: 'direction-flip', analysis, current, proposed: excluded };
  }

  const pivot = analysis.trigger;
  const stop = analysis.stop;
  const target = analysis.t1;
  const worst_entry =
    side === 'long'
      ? (analysis.entryHigh ?? round2(pivot * (1 + CHASE_PCT / 100)))
      : (analysis.entryLow ?? round2(pivot * (1 - CHASE_PCT / 100)));

  const sized = sizeFromProfile(profile, pivot, stop);
  const shares = sized?.shares ?? current?.shares ?? null;
  const riskDollars = sized?.risk_dollars ?? current?.risk_dollars ?? null;

  const proposed: WatchlistCandidate = {
    ticker: T,
    side,
    setup: current?.setup ?? `Analysis (${side})`,
    pivot,
    worst_entry,
    stop,
    target,
    shares,
    risk_dollars: riskDollars,
    score: current?.score ?? null,
    plan_type: current?.plan_type ?? 'analysis',
    validation_note: `From ticker-analysis (signals.md ${analysis.date})`,
    validated: true,
    source: 'analysis',
    t1: analysis.t1,
    t2: analysis.t2,
    t3: analysis.t3,
    screener_origin,
  };
  if (current?.thesis_id) proposed.thesis_id = current.thesis_id;

  let change: ReconcileChange;
  if (!current) change = 'new';
  else if (!near(current.pivot, pivot) || !near(current.stop, stop) || !near(current.target, target))
    change = 'levels-updated';
  else change = 'unchanged';

  return { ticker: T, change, analysis, current, proposed };
}

/**
 * Write the reconcile outcome into the watchlist (creating one if needed).
 * An `analysis-excluded` proposed candidate (direction-flip) is parked under
 * `rejected_by_validation`; anything else is upserted into `candidates` —
 * a same-direction analysis read re-promotes a chart-validation reject.
 */
export function applyReconcile(
  wl: Watchlist | null,
  proposed: WatchlistCandidate,
  date: string,
): Watchlist {
  const T = proposed.ticker.toUpperCase();
  const excluded = proposed.source === 'analysis-excluded';
  if (!wl) {
    return {
      workflow: 'ticker-analysis',
      date,
      exposure_decision: null,
      candidates: excluded ? [] : [proposed],
      rejected_by_validation: excluded ? [proposed] : [],
      notes: 'created from ticker-analysis',
      source_plan: null,
    };
  }
  const rejected = wl.rejected_by_validation.filter((c) => c.ticker.toUpperCase() !== T);
  if (excluded) {
    return {
      ...wl,
      candidates: wl.candidates.filter((c) => c.ticker.toUpperCase() !== T),
      rejected_by_validation: [...rejected, proposed],
    };
  }
  const idx = wl.candidates.findIndex((c) => c.ticker.toUpperCase() === T);
  const candidates = [...wl.candidates];
  if (idx >= 0) candidates[idx] = proposed;
  else candidates.push(proposed);
  return { ...wl, candidates, rejected_by_validation: rejected };
}
