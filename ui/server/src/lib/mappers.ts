import path from 'node:path';
import YAML from 'yaml';
import { basename, findLatest, listDir, readJson, readText, resolveFile } from './files';
import type {
  BottomFlowCandidate,
  BottomFlowResult,
  ExposureGate,
  ExposurePosture,
  MemoryResponse,
  MemoryThesis,
  PortfolioHeat,
  Position,
  RegimeComponent,
  RegimeComposite,
  ScreenerCandidate,
  ScreenerResult,
  Sourced,
  ThesisDetail,
  ThesisIndexEntry,
  Watchlist,
  WatchlistCandidate,
} from '@shared/types';

/* ---------------- small coercion helpers ---------------- */

export function numOrNull(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}
export function strOrNull(v: unknown): string | null {
  return typeof v === 'string' && v.length > 0 ? v : null;
}
export function asArray<T = unknown>(v: unknown): T[] {
  return Array.isArray(v) ? (v as T[]) : [];
}
export function asRecord(v: unknown): Record<string, unknown> {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

const sub = (dataDir: string, name: string) => path.join(dataDir, name);

function sourced<T>(file: string | null, data: T | null, date: string | null): Sourced<T> {
  return { date, source: basename(file), data };
}

/* ---------------- filename patterns ---------------- */

export const RE = {
  exposureDecision: /^exposure_decision_\d{4}-\d{2}-\d{2}\.json$/,
  exposurePosture: /^exposure_posture_\d{4}-\d{2}-\d{2}_\d+\.json$/,
  watchlist: /^watchlist_\d{4}-\d{2}-\d{2}\.json$/,
  watchlistValidation: /^watchlist_validation_\d{4}-\d{2}-\d{2}\.json$/,
  portfolioHeat: /^portfolio_heat_\d{4}-\d{2}-\d{2}_\d+\.json$/,
  breadth: /^market_breadth_\d{4}-\d{2}-\d{2}_\d+\.json$/,
  uptrend: /^uptrend_analysis_\d{4}-\d{2}-\d{2}_\d+\.json$/,
  top: /^market_top_\d{4}-\d{2}-\d{2}_\d+\.json$/,
  macro: /^macro_regime_\d{4}-\d{2}-\d{2}_\d+\.json$/,
  vcp: /^vcp_screener_\d{4}-\d{2}-\d{2}_\d+\.json$/,
  plan: /^breakout_trade_plan_\d{4}-\d{2}-\d{2}_\d+\.json$/,
  swingShort: /^swing_short_screener_\d{4}-\d{2}-\d{2}_\d+\.json$/,
  bottomFlow: /^bottom_flow_divergence_\d{4}-\d{2}-\d{2}_\d+\.json$/,
  weeklyReview: /^weekly_review_\d{4}-\d{2}-\d{2}\.json$/,
  monthlyReview: /^monthly_review_\d{4}-\d{2}-\d{2}\.json$/,
};

/* ---------------- exposure ---------------- */

export function getExposureGate(
  dataDir: string,
  date: string | null,
  source?: string | null,
): Sourced<ExposureGate> {
  const file = resolveFile(sub(dataDir, 'schedule'), RE.exposureDecision, date, source);
  const raw = asRecord(readJson(file));
  if (!file) return sourced<ExposureGate>(null, null, date);
  const gate: ExposureGate = {
    workflow: strOrNull(raw.workflow) ?? undefined,
    date: strOrNull(raw.date) ?? undefined,
    decision: strOrNull(raw.decision) ?? 'restrict',
    net_exposure_ceiling_pct: numOrNull(raw.net_exposure_ceiling_pct),
    rationale: strOrNull(raw.rationale),
    key_signals: asArray<string>(raw.key_signals).filter((s) => typeof s === 'string'),
  };
  return sourced(file, gate, date);
}

export function getPosture(dataDir: string, date: string | null): Sourced<ExposurePosture> {
  const file = findLatest(sub(dataDir, 'market'), RE.exposurePosture, date);
  const raw = asRecord(readJson(file));
  if (!file) return sourced<ExposurePosture>(null, null, date);
  const cs = asRecord(raw.component_scores);
  const posture: ExposurePosture = {
    generated_at: strOrNull(raw.generated_at) ?? undefined,
    exposure_ceiling_pct: numOrNull(raw.exposure_ceiling_pct),
    bias: strOrNull(raw.bias),
    participation: strOrNull(raw.participation),
    recommendation: strOrNull(raw.recommendation),
    confidence: strOrNull(raw.confidence),
    composite_score: numOrNull(raw.composite_score),
    component_scores: Object.fromEntries(
      Object.entries(cs).map(([k, v]) => [k, numOrNull(v) ?? 0]),
    ),
    inputs_provided: asArray<string>(raw.inputs_provided),
    inputs_missing: asArray<string>(raw.inputs_missing),
    rationale: strOrNull(raw.rationale),
  };
  return sourced(file, posture, date);
}

/* ---------------- watchlist ---------------- */

function mapWatchlistCandidate(c: Record<string, unknown>): WatchlistCandidate {
  const out: WatchlistCandidate = {
    ticker: String(c.ticker ?? ''),
    side: strOrNull(c.side) ?? 'long',
    setup: strOrNull(c.setup),
    pivot: numOrNull(c.pivot),
    worst_entry: numOrNull(c.worst_entry),
    stop: numOrNull(c.stop),
    target: numOrNull(c.target),
    shares: numOrNull(c.shares),
    risk_dollars: numOrNull(c.risk_dollars),
    score: numOrNull(c.score),
    plan_type: strOrNull(c.plan_type),
    validation_note: strOrNull(c.validation_note),
    validated: typeof c.validated === 'boolean' ? c.validated : null,
  };
  // Preserve reconcile-added fields when present (keeps screener candidates clean).
  if (typeof c.source === 'string') out.source = c.source;
  // The scheduler injects thesis_id; dropping it here would strip the journal
  // commands from the intraday OPEN signals after any UI watchlist rewrite.
  if (typeof c.thesis_id === 'string') out.thesis_id = c.thesis_id;
  if (c.t1 !== undefined) out.t1 = numOrNull(c.t1);
  if (c.t2 !== undefined) out.t2 = numOrNull(c.t2);
  if (c.t3 !== undefined) out.t3 = numOrNull(c.t3);
  if (c.screener_origin && typeof c.screener_origin === 'object' && !Array.isArray(c.screener_origin)) {
    out.screener_origin = c.screener_origin as WatchlistCandidate['screener_origin'];
  }
  return out;
}

export function getWatchlist(
  dataDir: string,
  date: string | null,
  source?: string | null,
): Sourced<Watchlist> {
  const dir = sub(dataDir, 'schedule');
  const file = resolveFile(dir, RE.watchlist, date, source);
  if (!file) return sourced<Watchlist>(null, null, date);
  const raw = asRecord(readJson(file));

  // Optionally enrich `validated` from the separate validation verdicts file.
  const valFile = findLatest(dir, RE.watchlistValidation, date);
  const verdicts = asArray<Record<string, unknown>>(asRecord(readJson(valFile)).verdicts);
  const verdictByTicker = new Map<string, { validated: boolean; note: string | null }>();
  for (const v of verdicts) {
    const t = strOrNull(v.ticker);
    if (t) {
      verdictByTicker.set(t, {
        validated: strOrNull(v.verdict) === 'pass',
        note: strOrNull(v.note),
      });
    }
  }
  const enrich = (cand: WatchlistCandidate): WatchlistCandidate => {
    if (cand.validated === null && verdictByTicker.has(cand.ticker)) {
      const v = verdictByTicker.get(cand.ticker)!;
      return { ...cand, validated: v.validated, validation_note: cand.validation_note ?? v.note };
    }
    return cand;
  };

  const watchlist: Watchlist = {
    workflow: strOrNull(raw.workflow) ?? undefined,
    date: strOrNull(raw.date) ?? undefined,
    exposure_decision: strOrNull(raw.exposure_decision),
    candidates: asArray<Record<string, unknown>>(raw.candidates).map(mapWatchlistCandidate).map(enrich),
    rejected_by_validation: asArray<Record<string, unknown>>(raw.rejected_by_validation).map(
      mapWatchlistCandidate,
    ),
    notes: strOrNull(raw.notes),
    source_plan: strOrNull(raw.source_plan),
  };
  return sourced(file, watchlist, date);
}

/** Read account sizing from trading_profile.json (repo root or data dir). */
export function readProfile(
  dataDir: string,
): { account_size: number; risk_pct: number; max_position_pct: number | null } | null {
  // ?? must apply to readJson (null when missing), not asRecord (never null) —
  // the old form short-circuited on `{}` and the real profile (the second
  // path, $TRADING_DATE_DIR/trading_profile.json) was never read.
  const raw = asRecord(
    readJson(path.join(dataDir, '..', 'trading_profile.json')) ??
      readJson(path.join(dataDir, 'trading_profile.json')),
  );
  if (Object.keys(raw).length === 0) return null;
  return {
    account_size: numOrNull(raw.account_size) ?? 0,
    risk_pct: numOrNull(raw.risk_pct) ?? 0,
    max_position_pct: numOrNull(raw.max_position_pct),
  };
}

/* ---------------- portfolio heat ---------------- */

function mapPosition(p: Record<string, unknown>): Position {
  return {
    ticker: String(p.ticker ?? ''),
    side: strOrNull(p.side) ?? 'long',
    entry_price: numOrNull(p.entry_price),
    stop_loss: numOrNull(p.stop_loss),
    current_price: numOrNull(p.current_price),
    shares: numOrNull(p.shares),
    position_size_dollars: numOrNull(p.position_size_dollars),
    risk_dollars: numOrNull(p.risk_dollars),
    sector: strOrNull(p.sector),
    entry_date: strOrNull(p.entry_date),
    days_held: numOrNull(p.days_held),
    pnl_pct: numOrNull(p.pnl_pct),
    pnl_dollars: numOrNull(p.pnl_dollars),
    mae_pct: numOrNull(p.mae_pct),
    mfe_pct: numOrNull(p.mfe_pct),
  };
}

export function getPortfolio(
  dataDir: string,
  date: string | null,
  source?: string | null,
): Sourced<PortfolioHeat> {
  const file = resolveFile(sub(dataDir, 'journal'), RE.portfolioHeat, date, source);
  if (!file) return sourced<PortfolioHeat>(null, null, date);
  const raw = asRecord(readJson(file));
  const sectorRaw = asRecord(raw.sector_exposure);
  const heat: PortfolioHeat = {
    generated_at: strOrNull(raw.generated_at),
    account_size: numOrNull(raw.account_size),
    open_risk_pct: numOrNull(raw.open_risk_pct),
    open_risk_dollars: numOrNull(raw.open_risk_dollars),
    positions_count: numOrNull(raw.positions_count),
    max_positions: numOrNull(raw.max_positions),
    remaining_position_slots: numOrNull(raw.remaining_position_slots),
    max_portfolio_heat_pct: numOrNull(raw.max_portfolio_heat_pct),
    remaining_heat_pct: numOrNull(raw.remaining_heat_pct),
    remaining_heat_dollars: numOrNull(raw.remaining_heat_dollars),
    sector_exposure: Object.fromEntries(
      Object.entries(sectorRaw).map(([k, v]) => [k, numOrNull(v) ?? 0]),
    ),
    positions: asArray<Record<string, unknown>>(raw.positions).map(mapPosition),
    warnings: asArray<string>(raw.warnings),
  };
  return sourced(file, heat, date);
}

/* ---------------- market regime ---------------- */

export function mapRegime(rawIn: unknown): RegimeComposite | null {
  const raw = asRecord(rawIn);
  if (Object.keys(raw).length === 0) return null;
  const comp = asRecord(raw.composite);
  const c = Object.keys(comp).length ? comp : raw;
  const cs = asRecord(c.component_scores ?? raw.component_scores);
  const components: RegimeComponent[] = Object.entries(cs).map(([key, v]) => {
    const rec = asRecord(v);
    return {
      key,
      label: strOrNull(rec.label) ?? key,
      score: numOrNull(typeof v === 'number' ? v : rec.score),
      weight: numOrNull(rec.weight),
    };
  });
  return {
    composite_score: numOrNull(c.composite_score),
    zone: strOrNull(c.zone),
    zone_color: strOrNull(c.zone_color),
    guidance: strOrNull(c.guidance ?? c.exposure_guidance ?? c.risk_budget),
    components,
    generated_at: strOrNull(asRecord(raw.metadata).generated_at ?? raw.generated_at),
  };
}

export function getRegime(
  dataDir: string,
  pattern: RegExp,
  date: string | null,
  source?: string | null,
): Sourced<RegimeComposite> {
  const file = resolveFile(sub(dataDir, 'market'), pattern, date, source);
  return sourced(file, mapRegime(readJson(file)), date);
}

/* ---------------- screeners ---------------- */

export function mapScreenerCandidate(cIn: Record<string, unknown>): ScreenerCandidate {
  const c = asRecord(cIn);
  const tp = asRecord(c.trade_plan);
  const tl = asRecord(c.trade_levels);
  const metricsRaw = asRecord(c.metrics);
  const metrics: Record<string, number | boolean | null> = {};
  for (const [k, v] of Object.entries(metricsRaw)) {
    metrics[k] = typeof v === 'number' || typeof v === 'boolean' ? v : null;
  }
  const components: Record<string, number> = {};
  for (const [k, v] of Object.entries(asRecord(c.components))) {
    components[k] = numOrNull(v) ?? 0;
  }
  return {
    symbol: String(c.symbol ?? ''),
    name: strOrNull(c.name),
    sector: strOrNull(c.sector),
    composite_score: numOrNull(c.composite_score),
    grade: strOrNull(c.grade),
    strongest_signal: strOrNull(c.strongest_signal),
    components,
    entry: numOrNull(tp.signal_entry ?? tl.entry),
    stop: numOrNull(tp.stop_loss_price ?? tl.stop),
    target: numOrNull(tp.target_price ?? tl.target_2r ?? tl.target),
    metrics,
    sector_etf: strOrNull(c.sector_etf),
    sector_rs: numOrNull(c.sector_rs),
    sector_leadership: strOrNull(c.sector_leadership),
    sector_fight: typeof c.sector_fight === 'boolean' ? c.sector_fight : null,
  };
}

/** Normalize a native screener file (`{meta, candidates[]}`) into a ScreenerResult. */
export function mapScreenerResult(rawIn: unknown, kind: 'vcp' | 'swing-short'): ScreenerResult {
  const raw = asRecord(rawIn);
  return {
    kind,
    meta: asRecord(raw.meta),
    candidates: asArray<Record<string, unknown>>(raw.candidates).map(mapScreenerCandidate),
  };
}

export function getScreener(
  dataDir: string,
  kind: 'vcp' | 'swing-short',
  date: string | null,
  source?: string | null,
): Sourced<ScreenerResult> {
  const pattern = kind === 'vcp' ? RE.vcp : RE.swingShort;
  const file = resolveFile(sub(dataDir, 'screeners'), pattern, date, source);
  if (!file) return sourced<ScreenerResult>(null, null, date);
  return sourced(file, mapScreenerResult(readJson(file), kind), date);
}

/* ---------------- bottom flow divergence screener ---------------- */

function strList(v: unknown): string[] {
  return asArray<unknown>(v).filter((x): x is string => typeof x === 'string');
}

/** Normalize one bottom-flow candidate (`{...tags, metrics{}}`) into a flat record. */
export function mapBottomFlowCandidate(cIn: Record<string, unknown>): BottomFlowCandidate {
  const c = asRecord(cIn);
  const m = asRecord(c.metrics);
  return {
    symbol: String(c.symbol ?? ''),
    sector: strOrNull(c.sector),
    grade: strOrNull(c.grade) ?? '',
    score: numOrNull(c.score),
    pct_off_low: numOrNull(m.pct_off_low),
    pct_off_high: numOrNull(m.pct_off_high),
    perf_y: numOrNull(m.perf_y),
    perf_6m: numOrNull(m.perf_6m),
    perf_3m: numOrNull(m.perf_3m),
    rsi: numOrNull(m.rsi),
    rev_ttm: numOrNull(m.rev_ttm),
    rev_qoq: numOrNull(m.rev_qoq),
    ocf: numOrNull(m.ocf),
    fcf: numOrNull(m.fcf),
    fcf_margin: numOrNull(m.fcf_margin),
    gross_margin: numOrNull(m.gross_margin),
    oper_margin: numOrNull(m.oper_margin),
    net_income: numOrNull(m.net_income),
    mfi: numOrNull(m.mfi),
    cmf: numOrNull(m.cmf),
    altman_z: numOrNull(m.altman_z),
    current_ratio: numOrNull(m.current_ratio),
    mkt_cap: numOrNull(m.mkt_cap),
    avg_vol: numOrNull(m.avg_vol),
    flow_profile: strList(c.flow_profile),
    turning: c.turning === true,
    survivable: c.survivable === true,
    organic_warn: c.organic_warn === true,
    risk_flags: strList(c.risk_flags),
    fundamental_ok: c.fundamental_ok === true,
    accumulation_ok: c.accumulation_ok === true,
  };
}

/** Normalize a native bottom-flow file (`{meta, candidates[]}`) into a BottomFlowResult. */
export function mapBottomFlowResult(rawIn: unknown): BottomFlowResult {
  const raw = asRecord(rawIn);
  return {
    meta: asRecord(raw.meta),
    candidates: asArray<Record<string, unknown>>(raw.candidates).map(mapBottomFlowCandidate),
  };
}

/* ---------------- theses ---------------- */

function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

export function getTheses(dataDir: string): ThesisIndexEntry[] {
  const idxFile = path.join(dataDir, 'journal', 'theses', '_index.json');
  const raw = asRecord(readJson(idxFile));
  const theses = asRecord(raw.theses);
  const today = todayISO();
  return Object.entries(theses).map(([id, vIn]) => {
    const v = asRecord(vIn);
    const nextReview = strOrNull(v.next_review_date);
    return {
      id,
      ticker: String(v.ticker ?? ''),
      status: strOrNull(v.status) ?? 'IDEA',
      thesis_type: strOrNull(v.thesis_type),
      created_at: strOrNull(v.created_at),
      updated_at: strOrNull(v.updated_at),
      next_review_date: nextReview,
      review_status: strOrNull(v.review_status),
      review_due: !!nextReview && nextReview <= today,
    };
  });
}

export function getThesisDetail(dataDir: string, id: string): ThesisDetail | null {
  // `id` is validated by the route; read the matching yaml file.
  const dir = path.join(dataDir, 'journal', 'theses');
  const fname = listDir(dir).find((n) => n === `${id}.yaml`);
  if (!fname) return null;
  const text = readText(path.join(dir, fname));
  if (text == null) return null;
  let raw: Record<string, unknown>;
  try {
    raw = asRecord(YAML.parse(text));
  } catch {
    return null;
  }
  return {
    id,
    ticker: String(raw.ticker ?? ''),
    status: strOrNull(raw.status) ?? 'IDEA',
    thesis_type: strOrNull(raw.thesis_type),
    setup_type: strOrNull(raw.setup_type),
    thesis_statement: strOrNull(raw.thesis_statement),
    entry: asRecord(raw.entry),
    exit: asRecord(raw.exit),
    monitoring: asRecord(raw.monitoring),
    origin: asRecord(raw.origin),
    outcome: asRecord(raw.outcome),
    raw,
  };
}

/** Full trader-memory-core overview: every thesis (detail) + a computed summary. */
export function getMemory(dataDir: string): MemoryResponse {
  const today = todayISO();
  const index = getTheses(dataDir);
  const theses: MemoryThesis[] = [];
  for (const e of index) {
    const detail = getThesisDetail(dataDir, e.id);
    if (!detail) continue;
    theses.push({
      ...detail,
      created_at: e.created_at,
      updated_at: e.updated_at,
      next_review_date: e.next_review_date,
      review_status: e.review_status,
      review_due: e.review_due,
    });
  }

  const byStatus: Record<string, number> = {};
  let active = 0;
  let closed = 0;
  let wins = 0;
  let realized = 0;
  let realizedSeen = false;
  for (const t of theses) {
    const st = t.status || 'IDEA';
    byStatus[st] = (byStatus[st] ?? 0) + 1;
    if (st === 'ACTIVE' || st === 'PARTIALLY_CLOSED') active += 1;
    if (st === 'CLOSED') {
      closed += 1;
      const pnl = numOrNull(asRecord(t.outcome).pnl_dollars);
      if (pnl != null) {
        realized += pnl;
        realizedSeen = true;
        if (pnl > 0) wins += 1;
      }
    }
  }

  return {
    today,
    summary: {
      total: theses.length,
      byStatus,
      reviewDue: theses.filter((t) => t.review_due).length,
      active,
      closed,
      wins,
      realizedPnl: realizedSeen ? realized : null,
    },
    theses,
  };
}
