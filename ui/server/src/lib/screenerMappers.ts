import type {
  ChecklistPoint,
  ChecklistResult,
  ExposureGate,
  PortfolioHeat,
  Sourced,
  StagedPlan,
  StagedPlanOrder,
  StagedPlanReject,
  StagedScreener,
  StagedScreenerCandidate,
  VcpComponents,
  VcpContraction,
  VcpPattern,
  VcpPivotProximity,
  VcpRelativeStrength,
  VcpRsPeriod,
  VcpTrendCriterion,
  VcpTrendTemplate,
  VcpVolumePattern,
} from '@shared/types';
import { asArray, asRecord, numOrNull, strOrNull } from './mappers';

/* Fixed composite weights — mirror scorer.COMPONENT_WEIGHTS (sum to 1.0). */
export const VCP_COMPONENT_WEIGHTS = {
  trend_template: 0.25,
  vcp_pattern: 0.25,
  volume_pattern: 0.2,
  pivot_proximity: 0.15,
  relative_strength: 0.15,
} as const;

const MIN_PRICE = 15;
const MIN_TURNOVER = 25_000_000; // $25M/day
const MAX_STOP_PCT = 8;
const RS_FLOOR = 70;
const DEFAULT_HEAT_CEILING = 6;
const DEFAULT_MAX_POSITIONS = 6;

function boolOrNull(v: unknown): boolean | null {
  return typeof v === 'boolean' ? v : null;
}

/* ---------------- VCP component coercion ---------------- */

function mapTrendTemplate(raw: Record<string, unknown>): VcpTrendTemplate {
  const criteria: Record<string, VcpTrendCriterion> = {};
  for (const [k, v] of Object.entries(asRecord(raw.criteria))) {
    const c = asRecord(v);
    criteria[k] = { passed: c.passed === true, detail: strOrNull(c.detail) };
  }
  return {
    score: numOrNull(raw.score),
    raw_score: numOrNull(raw.raw_score),
    passed: boolOrNull(raw.passed),
    sma50: numOrNull(raw.sma50),
    sma150: numOrNull(raw.sma150),
    sma200: numOrNull(raw.sma200),
    sma50_distance_pct: numOrNull(raw.sma50_distance_pct),
    criteria_passed: numOrNull(raw.criteria_passed),
    criteria_total: numOrNull(raw.criteria_total),
    criteria,
  };
}

function mapVcpPattern(raw: Record<string, unknown>): VcpPattern {
  const contractions: VcpContraction[] = asArray<Record<string, unknown>>(raw.contractions).map(
    (cIn) => {
      const c = asRecord(cIn);
      return {
        label: strOrNull(c.label),
        depth_pct: numOrNull(c.depth_pct),
        duration_days: numOrNull(c.duration_days),
        low_price: numOrNull(c.low_price),
        high_price: numOrNull(c.high_price),
      };
    },
  );
  const ratios = asArray(asRecord(raw.validation).contraction_ratios)
    .map(numOrNull)
    .filter((n): n is number => n !== null);
  return {
    score: numOrNull(raw.score),
    valid_vcp: boolOrNull(raw.valid_vcp),
    num_contractions: numOrNull(raw.num_contractions),
    pivot_price: numOrNull(raw.pivot_price),
    pattern_duration_days: numOrNull(raw.pattern_duration_days),
    contractions,
    contraction_ratios: ratios,
  };
}

function mapVolumePattern(raw: Record<string, unknown>): VcpVolumePattern {
  return {
    score: numOrNull(raw.score),
    dry_up_ratio: numOrNull(raw.dry_up_ratio),
    avg_volume_50d: numOrNull(raw.avg_volume_50d),
    breakout_volume_detected: boolOrNull(raw.breakout_volume_detected),
  };
}

function mapPivotProximity(raw: Record<string, unknown>): VcpPivotProximity {
  return {
    score: numOrNull(raw.score),
    distance_from_pivot_pct: numOrNull(raw.distance_from_pivot_pct),
    pivot_price: numOrNull(raw.pivot_price),
    stop_loss_price: numOrNull(raw.stop_loss_price),
    risk_pct: numOrNull(raw.risk_pct),
    trade_status: strOrNull(raw.trade_status),
  };
}

function mapRelativeStrength(raw: Record<string, unknown>): VcpRelativeStrength {
  const periods: VcpRsPeriod[] = asArray<Record<string, unknown>>(raw.period_details).map((pIn) => {
    const p = asRecord(pIn);
    return {
      period_days: numOrNull(p.period_days),
      weight: numOrNull(p.weight),
      relative_pct: numOrNull(p.relative_pct),
    };
  });
  return {
    score: numOrNull(raw.score),
    rs_rank_estimate: numOrNull(raw.rs_rank_estimate),
    rs_percentile: numOrNull(raw.rs_percentile),
    weighted_rs: numOrNull(raw.weighted_rs),
    period_details: periods,
  };
}

function mapComponents(raw: Record<string, unknown>): VcpComponents {
  return {
    trend_template: mapTrendTemplate(asRecord(raw.trend_template)),
    vcp_pattern: mapVcpPattern(asRecord(raw.vcp_pattern)),
    volume_pattern: mapVolumePattern(asRecord(raw.volume_pattern)),
    pivot_proximity: mapPivotProximity(asRecord(raw.pivot_proximity)),
    relative_strength: mapRelativeStrength(asRecord(raw.relative_strength)),
  };
}

/* ---------------- Plan coercion ---------------- */

function mapActionableOrder(raw: Record<string, unknown>): StagedPlanOrder {
  const tp = asRecord(raw.trade_plan);
  return {
    symbol: String(raw.symbol ?? '').toUpperCase(),
    plan_type: strOrNull(raw.plan_type),
    decision_code: strOrNull(raw.decision_code),
    decision_reason: strOrNull(raw.decision_reason),
    signal_entry: numOrNull(tp.signal_entry),
    worst_entry: numOrNull(tp.worst_entry),
    stop_loss_price: numOrNull(tp.stop_loss_price),
    target_price: numOrNull(tp.target_price),
    shares: numOrNull(tp.shares),
    risk_dollars: numOrNull(tp.risk_dollars),
    risk_pct_worst: numOrNull(tp.risk_pct_worst),
    cumulative_risk_pct: numOrNull(tp.cumulative_risk_pct),
    reward_risk_ratio: numOrNull(tp.reward_risk_ratio),
    earnings_date: strOrNull(raw.earnings_date),
    days_to_earnings: numOrNull(raw.days_to_earnings),
    earnings_gate: strOrNull(raw.earnings_gate),
    fundamental_gate: strOrNull(raw.fundamental_gate),
    eps_growth_yoy: numOrNull(raw.eps_growth_yoy),
    revenue_growth_yoy: numOrNull(raw.revenue_growth_yoy),
    c_score: numOrNull(raw.c_score),
    a_score: numOrNull(raw.a_score),
  };
}

/** Breakout-state revalidation advisory (flat fields, no trade_plan / no shares). */
function mapRevalidationOrder(raw: Record<string, unknown>): StagedPlanOrder {
  return {
    symbol: String(raw.symbol ?? '').toUpperCase(),
    plan_type: strOrNull(raw.plan_type),
    decision_code: strOrNull(raw.decision_code),
    decision_reason: strOrNull(raw.next_action),
    signal_entry: numOrNull(raw.pivot),
    worst_entry: numOrNull(raw.max_entry_price),
    stop_loss_price: numOrNull(raw.stop_loss_price),
    target_price: numOrNull(raw.target_price),
    shares: null,
    risk_dollars: null,
    risk_pct_worst: numOrNull(raw.risk_pct_worst),
    cumulative_risk_pct: null,
    reward_risk_ratio: null,
    earnings_date: strOrNull(raw.earnings_date),
    days_to_earnings: numOrNull(raw.days_to_earnings),
    earnings_gate: strOrNull(raw.earnings_gate),
    fundamental_gate: strOrNull(raw.fundamental_gate),
    eps_growth_yoy: numOrNull(raw.eps_growth_yoy),
    revenue_growth_yoy: numOrNull(raw.revenue_growth_yoy),
    c_score: numOrNull(raw.c_score),
    a_score: numOrNull(raw.a_score),
  };
}

function mapReject(raw: Record<string, unknown>): StagedPlanReject {
  return {
    symbol: String(raw.symbol ?? '').toUpperCase(),
    reason: strOrNull(raw.reason) ?? strOrNull(raw.blocked_reason) ?? strOrNull(raw.binding_constraint),
  };
}

export function mapStagedPlan(rawIn: unknown): StagedPlan {
  const raw = asRecord(rawIn);
  const s = asRecord(raw.summary);
  return {
    generated_at: strOrNull(raw.generated_at),
    summary: {
      actionable_count: numOrNull(s.actionable_count),
      revalidation_count: numOrNull(s.revalidation_count),
      watchlist_count: numOrNull(s.watchlist_count),
      rejected_count: numOrNull(s.rejected_count),
      deferred_count: numOrNull(s.deferred_count),
      constrained_count: numOrNull(s.constrained_count),
      blocked_earnings_count: numOrNull(s.blocked_earnings_count),
      total_risk_pct: numOrNull(s.total_risk_pct),
    },
    actionable: asArray<Record<string, unknown>>(raw.actionable_orders).map(mapActionableOrder),
    revalidation: asArray<Record<string, unknown>>(raw.revalidation).map(mapRevalidationOrder),
    rejected: asArray<Record<string, unknown>>(raw.rejected).map(mapReject),
    blocked_earnings: asArray<Record<string, unknown>>(raw.blocked_earnings).map(mapReject),
    deferred: asArray<Record<string, unknown>>(raw.deferred).map(mapReject),
    constrained: asArray<Record<string, unknown>>(raw.constrained).map(mapReject),
  };
}

/** Index the plan's sized orders by symbol (actionable preferred over revalidation). */
export function planOrdersBySymbol(plan: StagedPlan | null): Record<string, StagedPlanOrder> {
  const by: Record<string, StagedPlanOrder> = {};
  if (!plan) return by;
  for (const o of plan.revalidation) if (o.symbol) by[o.symbol] = o;
  for (const o of plan.actionable) if (o.symbol) by[o.symbol] = o; // actionable wins
  return by;
}

/* ---------------- 5.3 checklist ---------------- */

const pt = (key: string, label: string, state: ChecklistPoint['state'], detail: string | null): ChecklistPoint => ({
  key,
  label,
  state,
  detail,
});

/**
 * The 8-point "take / no-take" checklist (trading-plan.md §5.3). Tri-state:
 * `unknown` means not yet determinable (missing data, or a point that only a
 * built plan can answer) — never silently rendered as a pass.
 */
export function computeChecklist(
  cand: StagedScreenerCandidate,
  plan: StagedPlanOrder | null,
  gate: Sourced<ExposureGate>,
  heat: Sourced<PortfolioHeat>,
): ChecklistResult {
  const points: ChecklistPoint[] = [];
  const t = cand.components.trend_template;
  const rs = cand.components.relative_strength;
  const pp = cand.components.pivot_proximity;
  const vp = cand.components.volume_pattern;

  // 1. Gate = allow
  const decision = gate.data?.decision ?? null;
  points.push(
    decision == null
      ? pt('gate', 'Гейт = allow', 'unknown', 'нет gate-файла')
      : pt('gate', 'Гейт = allow', decision === 'allow' ? 'pass' : 'fail', `гейт = ${decision}`),
  );

  // 2. price > SMA50 > SMA200
  if (cand.price != null && t.sma50 != null && t.sma200 != null) {
    const ok = cand.price > t.sma50 && t.sma50 > t.sma200;
    points.push(
      pt('ma_chain', 'Цена > SMA50 > SMA200', ok ? 'pass' : 'fail',
        `$${cand.price} / SMA50 $${t.sma50} / SMA200 $${t.sma200}`),
    );
  } else {
    points.push(pt('ma_chain', 'Цена > SMA50 > SMA200', 'unknown', 'нет MA-данных'));
  }

  // 3. RS percentile >= 70
  if (rs.rs_percentile != null) {
    points.push(
      pt('rs', `RS-перцентиль ≥ ${RS_FLOOR}`, rs.rs_percentile >= RS_FLOOR ? 'pass' : 'fail',
        `RS %ile = ${rs.rs_percentile}`),
    );
  } else {
    points.push(pt('rs', `RS-перцентиль ≥ ${RS_FLOOR}`, 'unknown', 'нет RS-перцентиля'));
  }

  // 4. price >= $15 and turnover >= $25M/day
  if (cand.price != null && vp.avg_volume_50d != null) {
    const turnover = cand.price * vp.avg_volume_50d;
    const ok = cand.price >= MIN_PRICE && turnover >= MIN_TURNOVER;
    points.push(
      pt('liquidity', 'Цена ≥ $15, оборот ≥ $25M/день', ok ? 'pass' : 'fail',
        `$${cand.price}, оборот ~$${(turnover / 1e6).toFixed(1)}M`),
    );
  } else {
    points.push(pt('liquidity', 'Цена ≥ $15, оборот ≥ $25M/день', 'unknown', 'нет данных оборота'));
  }

  // 5. clear base & stop <= 8% from entry
  if (cand.valid_vcp === false) {
    points.push(pt('base', 'Чёткая база: стоп ≤ 8%', 'fail', 'valid_vcp = false'));
  } else if (pp.risk_pct != null) {
    points.push(
      pt('base', 'Чёткая база: стоп ≤ 8%', pp.risk_pct <= MAX_STOP_PCT ? 'pass' : 'fail',
        `риск до стопа ${pp.risk_pct.toFixed(1)}%`),
    );
  } else {
    points.push(pt('base', 'Чёткая база: стоп ≤ 8%', 'unknown', 'нет риска до стопа'));
  }

  // 6. earnings > 10 trading days (needs a plan run with the earnings gate)
  if (!plan || plan.earnings_gate == null) {
    points.push(pt('earnings', 'До отчёта > 10 т.д.', 'unknown', 'построй план (earnings-гейт)'));
  } else if (plan.earnings_gate === 'pass') {
    const d = plan.days_to_earnings;
    points.push(pt('earnings', 'До отчёта > 10 т.д.', 'pass', d != null ? `${d} т.д. до отчёта` : 'отчёт далеко'));
  } else if (plan.earnings_gate === 'blocked') {
    const d = plan.days_to_earnings;
    points.push(pt('earnings', 'До отчёта > 10 т.д.', 'fail', d != null ? `отчёт через ${d} т.д.` : 'отчёт близко'));
  } else {
    points.push(pt('earnings', 'До отчёта > 10 т.д.', 'unknown', 'дата отчёта не определена'));
  }

  // 7. post-trade heat <= 6% and positions <= 6
  if (!plan || heat.data == null) {
    points.push(pt('heat', 'После сделки: heat ≤ 6%, позиций ≤ 6', 'unknown', 'построй план + heat-леджер'));
  } else {
    const ceil = heat.data.max_portfolio_heat_pct ?? DEFAULT_HEAT_CEILING;
    const maxPos = heat.data.max_positions ?? DEFAULT_MAX_POSITIONS;
    const newRisk =
      plan.cumulative_risk_pct ?? (heat.data.open_risk_pct ?? 0) + (plan.risk_pct_worst ?? 0);
    const newPos = (heat.data.positions_count ?? 0) + 1;
    const ok = newRisk <= ceil && newPos <= maxPos;
    points.push(
      pt('heat', 'После сделки: heat ≤ 6%, позиций ≤ 6', ok ? 'pass' : 'fail',
        `heat ~${newRisk.toFixed(1)}% / ${ceil}%, позиций ${newPos}/${maxPos}`),
    );
  }

  // 8. fundamental quality-floor (needs a plan run with the fundamental gate)
  const fLabel = 'Фунд. флор: EPS ≥ 0, EPS+выручка не падают вместе';
  const fgPct = (x: number | null) => (x == null ? 'n/a' : `${x >= 0 ? '+' : ''}${x.toFixed(0)}%`);
  const fgDetail =
    plan && plan.eps_growth_yoy != null
      ? `EPS ${fgPct(plan.eps_growth_yoy)} / выручка ${fgPct(plan.revenue_growth_yoy)} YoY (C${plan.c_score ?? '–'}/A${plan.a_score ?? '–'})`
      : 'фундаментал учтён';
  if (!plan || plan.fundamental_gate == null) {
    points.push(pt('fundamental', fLabel, 'unknown', 'построй план (fundamental-гейт)'));
  } else if (plan.fundamental_gate === 'pass') {
    points.push(pt('fundamental', fLabel, 'pass', fgDetail));
  } else if (plan.fundamental_gate === 'blocked') {
    points.push(pt('fundamental', fLabel, 'fail', fgDetail));
  } else {
    points.push(pt('fundamental', fLabel, 'unknown', 'фундаментал недоступен'));
  }

  // 9. sector not lagging SPY (a long shouldn't fight a weak group)
  const slLabel = 'Сектор не отстаёт от SPY';
  const slPct =
    cand.sector_rs == null ? 'n/a' : `${cand.sector_rs >= 0 ? '+' : ''}${cand.sector_rs.toFixed(0)}%`;
  const slDetail = cand.sector_etf ? `${cand.sector_etf} ${slPct} vs SPY` : 'сектор неизвестен';
  if (cand.sector_leadership == null) {
    points.push(
      pt('sector', slLabel, 'unknown', cand.sector_etf ? slDetail : 'нет данных по сектору'),
    );
  } else if (cand.sector_leadership === 'lagging') {
    points.push(pt('sector', slLabel, 'fail', `${slDetail} — отстаёт → лонг капается до Developing`));
  } else {
    points.push(
      pt(
        'sector',
        slLabel,
        'pass',
        `${slDetail}${cand.sector_leadership === 'leading' ? ' — лидирует' : ''}`,
      ),
    );
  }

  const knownPass = points.filter((p) => p.state === 'pass').length;
  return { points, allPass: points.every((p) => p.state === 'pass'), knownPass, total: points.length };
}

/* ---------------- Top-level staged screener ---------------- */

function mapCandidate(
  raw: Record<string, unknown>,
  plan: StagedPlanOrder | null,
  gate: Sourced<ExposureGate>,
  heat: Sourced<PortfolioHeat>,
): StagedScreenerCandidate {
  const base: StagedScreenerCandidate = {
    symbol: String(raw.symbol ?? '').toUpperCase(),
    sector: strOrNull(raw.sector),
    price: numOrNull(raw.price),
    composite_score: numOrNull(raw.composite_score),
    rating: strOrNull(raw.rating),
    quality_rating: strOrNull(raw.quality_rating),
    execution_state: strOrNull(raw.execution_state),
    execution_state_reasons: asArray<string>(raw.execution_state_reasons).filter(
      (s) => typeof s === 'string',
    ),
    valid_vcp: boolOrNull(raw.valid_vcp),
    entry_ready: boolOrNull(raw.entry_ready),
    state_cap_applied: boolOrNull(raw.state_cap_applied),
    cap_reason: strOrNull(raw.cap_reason),
    sector_etf: strOrNull(raw.sector_etf),
    sector_rs: numOrNull(raw.sector_rs),
    sector_leadership: strOrNull(raw.sector_leadership),
    weakest_component: strOrNull(raw.weakest_component),
    strongest_component: strOrNull(raw.strongest_component),
    components: mapComponents(raw),
    plan,
    checklist: { points: [], allPass: false, knownPass: 0, total: 0 },
  };
  base.checklist = computeChecklist(base, plan, gate, heat);
  return base;
}

const TOP_N = 100;

/**
 * Map a native VCP screener file (`{metadata, results[], summary}`) into the
 * rich staged view: top-100 candidates by composite_score, each joined with its
 * breakout-plan order (if any) and its 5.3 checklist.
 */
export function mapStagedScreener(
  rawIn: unknown,
  source: string | null,
  plan: StagedPlan | null,
  gate: Sourced<ExposureGate>,
  heat: Sourced<PortfolioHeat>,
): StagedScreener {
  const raw = asRecord(rawIn);
  const meta = asRecord(raw.metadata);
  const summary = asRecord(raw.summary);
  const bySymbol = planOrdersBySymbol(plan);

  const results = asArray<Record<string, unknown>>(raw.results)
    .map(asRecord)
    .sort((a, b) => (numOrNull(b.composite_score) ?? 0) - (numOrNull(a.composite_score) ?? 0))
    .slice(0, TOP_N)
    .map((r) => mapCandidate(r, bySymbol[String(r.symbol ?? '').toUpperCase()] ?? null, gate, heat));

  const funnel: Record<string, number> = {};
  for (const [k, v] of Object.entries(asRecord(meta.funnel))) {
    const n = numOrNull(v);
    if (n != null) funnel[k] = n;
  }

  return {
    source,
    meta: {
      generated_at: strOrNull(meta.generated_at),
      universe_description: strOrNull(meta.universe_description),
      funnel,
      total: numOrNull(summary.total),
    },
    candidates: results,
  };
}
