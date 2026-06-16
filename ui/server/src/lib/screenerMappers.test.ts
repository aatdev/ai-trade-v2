import { describe, expect, it } from 'vitest';
import type {
  ExposureGate,
  PortfolioHeat,
  Sourced,
  StagedPlan,
  StagedPlanOrder,
} from '@shared/types';
import { mapStagedPlan, mapStagedScreener } from './screenerMappers';

/* ---------------- fixtures ---------------- */

function makeResult(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    symbol: 'NVDA',
    sector: 'Technology',
    price: 100,
    composite_score: 85,
    rating: 'Strong VCP',
    quality_rating: 'Strong VCP',
    execution_state: 'Pre-breakout',
    valid_vcp: true,
    trend_template: { score: 90, sma50: 90, sma150: 80, sma200: 70, criteria: {
      c1_price_above_sma150_200: { passed: true, detail: 'ok' },
    } },
    vcp_pattern: { score: 70, valid_vcp: true, pivot_price: 102, contractions: [
      { label: 'T1', depth_pct: 15, duration_days: 6, low_price: 88, high_price: 103 },
    ], validation: { contraction_ratios: [0.55] } },
    volume_pattern: { score: 60, dry_up_ratio: 0.5, avg_volume_50d: 1_000_000 },
    pivot_proximity: { score: 80, distance_from_pivot_pct: -1, pivot_price: 102, stop_loss_price: 95, risk_pct: 5 },
    relative_strength: { score: 90, rs_percentile: 92, weighted_rs: 50, period_details: [
      { period_days: 63, weight: 0.4, relative_pct: 30 },
    ] },
    ...overrides,
  };
}

const gate = (decision: string | null): Sourced<ExposureGate> => ({
  date: null,
  source: 'exposure_decision_2026-06-16.json',
  data:
    decision == null
      ? null
      : { decision, net_exposure_ceiling_pct: null, rationale: null, key_signals: [] },
});

const heat = (over: Partial<PortfolioHeat> | null): Sourced<PortfolioHeat> => ({
  date: null,
  source: 'portfolio_heat.json',
  data:
    over == null
      ? null
      : ({
          open_risk_pct: 2,
          positions_count: 2,
          max_portfolio_heat_pct: 6,
          max_positions: 6,
          sector_exposure: {},
          positions: [],
          warnings: [],
          ...over,
        } as PortfolioHeat),
});

const order = (over: Partial<StagedPlanOrder> = {}): StagedPlanOrder => ({
  symbol: 'NVDA',
  plan_type: 'pending_breakout',
  decision_code: 'ACTIONABLE_PREBREAKOUT',
  decision_reason: null,
  signal_entry: 100,
  worst_entry: 102,
  stop_loss_price: 95,
  target_price: 110,
  shares: 100,
  risk_dollars: 500,
  risk_pct_worst: 1.5,
  cumulative_risk_pct: 3.5,
  reward_risk_ratio: 2,
  earnings_date: '2026-08-01',
  days_to_earnings: 33,
  earnings_gate: 'pass',
  fundamental_gate: 'pass',
  eps_growth_yoy: 22,
  revenue_growth_yoy: 16,
  c_score: 60,
  a_score: 70,
  ...over,
});

const planWith = (...orders: StagedPlanOrder[]): StagedPlan => ({
  generated_at: null,
  summary: {
    actionable_count: orders.length,
    revalidation_count: 0,
    watchlist_count: 0,
    rejected_count: 0,
    deferred_count: 0,
    constrained_count: 0,
    blocked_earnings_count: 0,
    total_risk_pct: null,
  },
  actionable: orders,
  revalidation: [],
  rejected: [],
  blocked_earnings: [],
  deferred: [],
  constrained: [],
});

function checklistFor(result: Record<string, unknown>, plan: StagedPlan | null, g = gate('allow'), h = heat({})) {
  const screener = mapStagedScreener({ results: [result], summary: { total: 1 } }, 'vcp_x.json', plan, g, h);
  return screener.candidates[0].checklist;
}

/* ---------------- mapStagedScreener ---------------- */

describe('mapStagedScreener', () => {
  it('sorts by composite_score desc and slices to top 100', () => {
    const results = Array.from({ length: 130 }, (_, i) => makeResult({ symbol: `T${i}`, composite_score: i }));
    const s = mapStagedScreener({ results, summary: { total: 130 } }, 's.json', null, gate('allow'), heat(null));
    expect(s.candidates).toHaveLength(100);
    expect(s.candidates[0].composite_score).toBe(129); // highest first
    expect(s.candidates[99].composite_score).toBe(30);
  });

  it('coerces nested components and joins the plan order by symbol', () => {
    const s = mapStagedScreener(
      { results: [makeResult()], summary: { total: 1 } },
      's.json',
      planWith(order()),
      gate('allow'),
      heat({}),
    );
    const c = s.candidates[0];
    expect(c.components.trend_template.sma50).toBe(90);
    expect(c.components.vcp_pattern.contraction_ratios).toEqual([0.55]);
    expect(c.components.relative_strength.rs_percentile).toBe(92);
    expect(c.plan?.shares).toBe(100);
  });
});

/* ---------------- computeChecklist ---------------- */

describe('computeChecklist', () => {
  it('all 8 points pass for a clean candidate with a passing plan', () => {
    const cl = checklistFor(makeResult(), planWith(order()));
    expect(cl.allPass).toBe(true);
    expect(cl.knownPass).toBe(8);
    expect(cl.total).toBe(8);
  });

  it('marks earnings + heat unknown when no plan is built', () => {
    const cl = checklistFor(makeResult(), null);
    const by = Object.fromEntries(cl.points.map((p) => [p.key, p.state]));
    expect(by.earnings).toBe('unknown');
    expect(by.fundamental).toBe('unknown');
    expect(by.heat).toBe('unknown');
    expect(by.gate).toBe('pass'); // gate/ma/rs/liquidity/base still resolve from screener alone
    expect(by.ma_chain).toBe('pass');
    expect(cl.allPass).toBe(false);
  });

  it('gate fails when decision != allow, unknown when no gate file', () => {
    expect(checklistFor(makeResult(), null, gate('restrict')).points.find((p) => p.key === 'gate')?.state).toBe(
      'fail',
    );
    expect(checklistFor(makeResult(), null, gate(null)).points.find((p) => p.key === 'gate')?.state).toBe(
      'unknown',
    );
  });

  it('ma_chain fails when price is below SMA50, unknown when SMAs missing', () => {
    expect(
      checklistFor(makeResult({ price: 50 }), null).points.find((p) => p.key === 'ma_chain')?.state,
    ).toBe('fail');
    expect(
      checklistFor(makeResult({ trend_template: { score: 90 } }), null).points.find(
        (p) => p.key === 'ma_chain',
      )?.state,
    ).toBe('unknown');
  });

  it('rs fails below 70, unknown when percentile missing', () => {
    expect(
      checklistFor(makeResult({ relative_strength: { rs_percentile: 40 } }), null).points.find(
        (p) => p.key === 'rs',
      )?.state,
    ).toBe('fail');
    expect(
      checklistFor(makeResult({ relative_strength: {} }), null).points.find((p) => p.key === 'rs')?.state,
    ).toBe('unknown');
  });

  it('base fails when valid_vcp is false or stop risk exceeds 8%', () => {
    expect(
      checklistFor(makeResult({ valid_vcp: false }), null).points.find((p) => p.key === 'base')?.state,
    ).toBe('fail');
    expect(
      checklistFor(makeResult({ pivot_proximity: { risk_pct: 12 } }), null).points.find(
        (p) => p.key === 'base',
      )?.state,
    ).toBe('fail');
  });

  it('liquidity fails when price < $15', () => {
    expect(
      checklistFor(makeResult({ price: 10 }), null).points.find((p) => p.key === 'liquidity')?.state,
    ).toBe('fail');
  });

  it('earnings fails when the plan gate is blocked', () => {
    const cl = checklistFor(makeResult(), planWith(order({ earnings_gate: 'blocked', days_to_earnings: 3 })));
    expect(cl.points.find((p) => p.key === 'earnings')?.state).toBe('fail');
  });

  it('fundamental fails when the plan gate is blocked', () => {
    const cl = checklistFor(
      makeResult(),
      planWith(order({ fundamental_gate: 'blocked', eps_growth_yoy: -16, revenue_growth_yoy: -5 })),
    );
    expect(cl.points.find((p) => p.key === 'fundamental')?.state).toBe('fail');
  });

  it('heat fails when cumulative risk exceeds the ceiling', () => {
    const cl = checklistFor(makeResult(), planWith(order({ cumulative_risk_pct: 9 })), gate('allow'), heat({}));
    expect(cl.points.find((p) => p.key === 'heat')?.state).toBe('fail');
  });
});

/* ---------------- mapStagedPlan ---------------- */

describe('mapStagedPlan', () => {
  it('flattens actionable orders and maps reject buckets', () => {
    const plan = mapStagedPlan({
      generated_at: '2026-06-16T00:00:00',
      summary: { actionable_count: 1, rejected_count: 1 },
      actionable_orders: [
        {
          symbol: 'nvda',
          plan_type: 'pending_breakout',
          trade_plan: { signal_entry: 100, stop_loss_price: 95, shares: 50, risk_pct_worst: 1.2 },
          earnings_gate: 'pass',
          days_to_earnings: 20,
        },
      ],
      rejected: [{ symbol: 'bad', reason: 'risk_pct_worst=14%>8%' }],
      blocked_earnings: [{ symbol: 'soon', blocked_reason: 'earnings in 3 trading days' }],
    });
    expect(plan.actionable[0].symbol).toBe('NVDA');
    expect(plan.actionable[0].signal_entry).toBe(100);
    expect(plan.actionable[0].shares).toBe(50);
    expect(plan.rejected[0]).toEqual({ symbol: 'BAD', reason: 'risk_pct_worst=14%>8%' });
    expect(plan.blocked_earnings[0].reason).toBe('earnings in 3 trading days');
  });
});
