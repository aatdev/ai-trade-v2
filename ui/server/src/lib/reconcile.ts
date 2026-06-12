import type {
  AnalysisSignal,
  ReconcileChange,
  ReconcileResult,
  ScreenerOrigin,
  Watchlist,
  WatchlistCandidate,
} from '@shared/types';

const EPS = 0.005; // price-equality tolerance

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
 * Decide how the analysis signal should change the watchlist candidate.
 *
 * Rule: the analysis signal (the deep, current read saved to signals.md) is
 * authoritative for DIRECTION and price LEVELS. We keep the same dollar-risk by
 * re-deriving shares = risk_dollars / |trigger − stop|, preserve the original
 * screener values under `screener_origin`, and tag `source: 'analysis'`.
 */
export function reconcile(
  wl: Watchlist | null,
  ticker: string,
  analysis: AnalysisSignal | null,
  profile: { account_size: number; risk_pct: number } | null,
): ReconcileResult {
  const T = ticker.toUpperCase();
  const current = findCandidate(wl, T);

  if (!analysis) {
    return { ticker: T, change: 'no-analysis', analysis: null, current, proposed: null };
  }

  const side = analysis.direction;
  const pivot = analysis.trigger;
  const stop = analysis.stop;
  const target = analysis.t1;
  const worst_entry =
    side === 'long' ? (analysis.entryHigh ?? pivot) : (analysis.entryLow ?? pivot);

  const riskDollars =
    current?.risk_dollars ??
    (profile && profile.account_size > 0 && profile.risk_pct > 0
      ? Math.round(profile.account_size * (profile.risk_pct / 100))
      : null);
  const dist = Math.abs(pivot - stop);
  const shares =
    riskDollars != null && dist > 0 ? Math.round(riskDollars / dist) : (current?.shares ?? null);

  // Preserve the *original* screener snapshot across repeated reconciles.
  const screener_origin =
    current?.screener_origin ??
    (current ? originSnapshot(current, wl?.source_plan ?? null) : null);

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

  let change: ReconcileChange;
  if (!current) change = 'new';
  else if ((current.side || '').toLowerCase() !== side) change = 'direction-flip';
  else if (!near(current.pivot, pivot) || !near(current.stop, stop) || !near(current.target, target))
    change = 'levels-updated';
  else change = 'unchanged';

  return { ticker: T, change, analysis, current, proposed };
}

/** Upsert the proposed candidate into the watchlist (creating one if needed). */
export function applyReconcile(
  wl: Watchlist | null,
  proposed: WatchlistCandidate,
  date: string,
): Watchlist {
  const T = proposed.ticker.toUpperCase();
  if (!wl) {
    return {
      workflow: 'ticker-analysis',
      date,
      exposure_decision: null,
      candidates: [proposed],
      rejected_by_validation: [],
      notes: 'created from ticker-analysis',
      source_plan: null,
    };
  }
  const rejected = wl.rejected_by_validation.filter((c) => c.ticker.toUpperCase() !== T);
  const idx = wl.candidates.findIndex((c) => c.ticker.toUpperCase() === T);
  const candidates = [...wl.candidates];
  if (idx >= 0) candidates[idx] = proposed;
  else candidates.push(proposed);
  return { ...wl, candidates, rejected_by_validation: rejected };
}
