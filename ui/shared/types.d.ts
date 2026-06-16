/**
 * Shared API response types for the trading-state dashboard.
 *
 * These are TYPE-ONLY declarations (no runtime code), imported by both the
 * Express server and the React client via the `@shared/*` path alias. Because
 * they are consumed through `import type { ... }`, the imports are erased at
 * runtime, so no bundling/compilation of this file is required.
 */

export type ExposureDecision = 'allow' | 'restrict' | 'cash-priority' | string;
export type Side = 'long' | 'short' | string;
export type ThesisStatus = 'IDEA' | 'OPEN' | 'CLOSED' | string;

/* ---------------- Auth ---------------- */

/** GET /api/auth — whether login is required and whether this client is in. */
export interface AuthStatusResponse {
  /** Login is configured (UI_AUTH_USER + UI_AUTH_PASSWORD set in .env). */
  authRequired: boolean;
  /** True when a valid session cookie is present, or when auth is disabled. */
  authenticated: boolean;
  /** Logged-in user name (only when authenticated against an enabled config). */
  user?: string;
}

/** POST /api/login, POST /api/logout */
export interface AuthActionResponse {
  ok: boolean;
  error?: string;
}

/** GET /api/dates */
export interface DatesResponse {
  dates: string[]; // descending (newest first)
  latest: string | null;
}

/**
 * GET /api/versions?kind=<kind> — the last ~10 file basenames for a selectable
 * data kind, newest first. Pin one via the matching `?source=`/`*Source=` param
 * on the kind's state endpoint. Valid kinds: exposure, watchlist, portfolio,
 * vcp, swing-short, breadth, uptrend, top, macro.
 */
export interface VersionsResponse {
  kind: string;
  versions: string[];
}

/** Wraps every state response so the client knows which date/file it got. */
export interface Sourced<T> {
  date: string | null; // the date actually resolved
  source: string | null; // basename of the file read (null if missing)
  data: T | null;
}

/* ---------------- Exposure gate ---------------- */

export interface ExposureGate {
  workflow?: string;
  date?: string;
  decision: ExposureDecision;
  net_exposure_ceiling_pct: number | null;
  rationale: string | null;
  key_signals: string[];
}

export interface ExposurePosture {
  generated_at?: string;
  exposure_ceiling_pct: number | null;
  bias: string | null;
  participation: string | null;
  recommendation: string | null;
  confidence: string | null;
  composite_score: number | null;
  component_scores: Record<string, number>;
  inputs_provided: string[];
  inputs_missing: string[];
  rationale: string | null;
}

export interface ExposureResponse {
  gate: Sourced<ExposureGate>;
  posture: Sourced<ExposurePosture>;
}

/* ---------------- Watchlist ---------------- */

export type CandidateSource = 'screener' | 'analysis' | 'analysis-excluded' | string;

/** Snapshot of the original screener-derived values, kept after a reconcile. */
export interface ScreenerOrigin {
  side: Side;
  pivot: number | null;
  stop: number | null;
  target: number | null;
  shares: number | null;
  score: number | null;
  source_plan: string | null;
}

export interface WatchlistCandidate {
  ticker: string;
  side: Side;
  setup: string | null;
  pivot: number | null;
  worst_entry: number | null;
  stop: number | null;
  target: number | null;
  shares: number | null;
  risk_dollars: number | null;
  score: number | null;
  plan_type: string | null;
  validation_note: string | null;
  validated: boolean | null;
  /** Where the current levels came from. Absent ⇒ treat as 'screener'. */
  source?: CandidateSource;
  t1?: number | null;
  t2?: number | null;
  t3?: number | null;
  screener_origin?: ScreenerOrigin | null;
  /** Injected by the scheduler's thesis-ingest; must survive UI rewrites. */
  thesis_id?: string | null;
}

export interface Watchlist {
  workflow?: string;
  date?: string;
  exposure_decision: ExposureDecision | null;
  candidates: WatchlistCandidate[];
  rejected_by_validation: WatchlistCandidate[];
  notes: string | null;
  source_plan: string | null;
}

/** A trade signal parsed from an analysis block in signals.md. */
export interface AnalysisSignal {
  ticker: string;
  date: string;
  direction: 'long' | 'short';
  trigger: number;
  stop: number;
  t1: number;
  t2: number | null;
  t3: number | null;
  entryLow: number | null;
  entryHigh: number | null;
}

export type ReconcileChange =
  | 'no-analysis'
  | 'new'
  | 'unchanged'
  | 'levels-updated'
  | 'direction-flip';

/** Comparison of the screener watchlist candidate vs the analysis signal. */
export interface ReconcileResult {
  ticker: string;
  change: ReconcileChange;
  analysis: AnalysisSignal | null;
  current: WatchlistCandidate | null;
  proposed: WatchlistCandidate | null;
}

export interface ApplyReconcileResponse {
  result: ReconcileResult;
  applied: boolean;
  watchlist: Watchlist | null;
}

/* ---------------- Portfolio heat / open positions ---------------- */

export interface Position {
  ticker: string;
  side: Side;
  entry_price: number | null;
  stop_loss: number | null;
  current_price: number | null;
  shares: number | null;
  position_size_dollars: number | null;
  risk_dollars: number | null;
  sector: string | null;
  entry_date: string | null;
  days_held: number | null;
  pnl_pct: number | null;
  pnl_dollars: number | null;
  mae_pct: number | null;
  mfe_pct: number | null;
}

export interface PortfolioHeat {
  generated_at: string | null;
  account_size: number | null;
  open_risk_pct: number | null;
  open_risk_dollars: number | null;
  positions_count: number | null;
  max_positions: number | null;
  remaining_position_slots: number | null;
  max_portfolio_heat_pct: number | null;
  remaining_heat_pct: number | null;
  remaining_heat_dollars: number | null;
  sector_exposure: Record<string, number>;
  positions: Position[];
  warnings: string[];
}

/* ---------------- Interactive Brokers live snapshot ---------------- */

/** One live position from Interactive Brokers (read-only Client Portal API). */
export interface IbPosition {
  symbol: string;
  conid: number | null;
  position: number | null; // signed quantity (negative = short)
  side: Side | null;
  avg_cost: number | null;
  market_price: number | null;
  market_value: number | null;
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
  realized_pnl: number | null;
  currency: string | null;
  asset_class: string | null;
  sector: string | null;
}

/** One live/working order from Interactive Brokers (read-only Client Portal API). */
export interface IbOrder {
  order_id: string | null;
  symbol: string;
  conid: number | null;
  side: string | null; // "BUY" | "SELL"
  order_type: string | null; // LMT / MKT / STP / STP LMT ...
  status: string | null; // Submitted / PreSubmitted / Filled / Cancelled ...
  total_quantity: number | null;
  filled_quantity: number | null;
  remaining_quantity: number | null;
  limit_price: number | null;
  stop_price: number | null;
  tif: string | null; // DAY / GTC ...
  currency: string | null;
  last_execution_time: string | null;
  order_desc: string | null;
  // Native-bracket linkage (optional — absent on older snapshots). The UI
  // collapses legs that share any of these tokens into a single bracket row.
  parent_id?: string | null; // child leg → parent's id/cOID
  client_order_id?: string | null; // parent leg cOID (idempotency anchor)
  order_ref?: string | null; // Gateway echo of cOID on some builds
  oca_group?: string | null; // OCA group shared by armed child legs
}

/** One executed trade (fill) from Interactive Brokers recent history. */
export interface IbTrade {
  execution_id: string | null;
  symbol: string;
  conid: number | null;
  side: string | null; // "BUY" | "SELL"
  quantity: number | null;
  price: number | null;
  amount: number | null; // net amount / proceeds
  commission: number | null;
  exchange: string | null;
  sec_type: string | null;
  trade_time: string | null; // ISO-8601 when derivable, else raw exchange string
  order_desc: string | null;
}

/** Account-level balances from Interactive Brokers. */
export interface IbAccountSummary {
  account_id: string | null;
  net_liquidation: number | null;
  total_cash: number | null;
  available_funds: number | null;
  buying_power: number | null;
  gross_position_value: number | null;
  unrealized_pnl: number | null;
  realized_pnl: number | null;
  excess_liquidity: number | null;
  equity_with_loan: number | null;
  currency: string | null;
}

/**
 * GET /api/ib — a live, read-only Interactive Brokers snapshot. `ok` is false
 * (with a human `error`) when the bundled IB Gateway is down or unauthenticated,
 * so the UI degrades gracefully instead of erroring.
 */
export interface IbSnapshot {
  ok: boolean;
  generated_at: string | null;
  mode: string | null; // "paper" | "live"
  account_id: string | null;
  account_ids: string[];
  summary: IbAccountSummary | null;
  positions: IbPosition[];
  orders: IbOrder[];
  trades: IbTrade[];
  error: string | null;
  source: string | null; // "live" | "fixture"
}

/* ---------------- Market regime ---------------- */

export interface RegimeComponent {
  key: string;
  label: string;
  score: number | null;
  weight: number | null;
}

export interface RegimeComposite {
  composite_score: number | null;
  zone: string | null;
  zone_color: string | null;
  guidance: string | null;
  components: RegimeComponent[];
  generated_at: string | null;
}

export interface MarketResponse {
  breadth: Sourced<RegimeComposite>;
  uptrend: Sourced<RegimeComposite>;
  top: Sourced<RegimeComposite>;
  macro: Sourced<RegimeComposite>;
  posture: Sourced<ExposurePosture>;
}

/* ---------------- Screeners ---------------- */

export interface ScreenerCandidate {
  symbol: string;
  name: string | null;
  sector: string | null;
  composite_score: number | null;
  grade: string | null;
  strongest_signal: string | null;
  components: Record<string, number>;
  /** Normalized entry/stop/target regardless of screener-specific key names. */
  entry: number | null;
  stop: number | null;
  target: number | null;
  metrics: Record<string, number | boolean | null>;
}

export interface ScreenerResult {
  kind: 'vcp' | 'swing-short';
  meta: Record<string, unknown>;
  candidates: ScreenerCandidate[];
}

export interface ScreenersResponse {
  vcp: Sourced<ScreenerResult>;
  swingShort: Sourced<ScreenerResult>;
}

/* ---------------- Theses ---------------- */

export interface ThesisIndexEntry {
  id: string;
  ticker: string;
  status: ThesisStatus;
  thesis_type: string | null;
  created_at: string | null;
  updated_at: string | null;
  next_review_date: string | null;
  review_status: string | null;
  review_due: boolean;
}

export interface ThesesResponse {
  theses: ThesisIndexEntry[];
}

/** Bulk hard-delete request: thesis ids must currently be IDEA / ENTRY_READY / INVALIDATED. */
export interface DeleteThesesRequest {
  ids: string[];
}

export interface ThesisDetail {
  id: string;
  ticker: string;
  status: ThesisStatus;
  thesis_type: string | null;
  setup_type: string | null;
  thesis_statement: string | null;
  entry: Record<string, unknown> | null;
  exit: Record<string, unknown> | null;
  monitoring: Record<string, unknown> | null;
  origin: Record<string, unknown> | null;
  outcome: Record<string, unknown> | null;
  raw: Record<string, unknown>;
}

/* ---------------- Trader memory (trader-memory-core) ---------------- */

/** A full thesis record plus the index-derived review fields. */
export interface MemoryThesis extends ThesisDetail {
  created_at: string | null;
  updated_at: string | null;
  next_review_date: string | null;
  review_status: string | null;
  review_due: boolean;
}

export interface MemorySummary {
  total: number;
  byStatus: Record<string, number>;
  reviewDue: number;
  active: number;
  closed: number;
  wins: number;
  realizedPnl: number | null;
}

/** GET /api/memory — trader-memory-core overview. */
export interface MemoryResponse {
  today: string;
  summary: MemorySummary;
  theses: MemoryThesis[];
}

/** GET /api/skill-doc/:skill — markdown docs bundled with a skill. */
export interface SkillDocSection {
  name: string; // e.g. "SKILL.md", "references/thesis_lifecycle.md"
  content: string;
}

export interface SkillDocResponse {
  skill: string;
  docs: SkillDocSection[];
}

/* ---------------- Signals journal ---------------- */

export interface SignalBlock {
  id: string;
  date: string;
  ticker: string;
  heading: string; // heading line without the leading "## "
  status: string | null;
  markdown: string; // full block markdown (includes its own "## …" heading)
}

export interface SignalsResponse {
  content: string;
  present: boolean;
  signals: SignalBlock[];
}

export interface DeleteSignalResponse {
  removed: number;
  kept: number;
  ticker: string;
  date: string;
  found: boolean;
}

/* ---------------- Autopilot / schedule ---------------- */

export interface AutopilotState {
  date: string | null;
  last_gate_decision: ExposureDecision | null;
  slots: Record<string, unknown>;
  weekly: Record<string, unknown>;
  monthly: Record<string, unknown>;
  intraday: Record<string, unknown>;
}

export interface AutopilotResponse {
  state: AutopilotState | null;
  weeklyReview: Sourced<Record<string, unknown>>;
  monthlyReview: Sourced<Record<string, unknown>>;
  logTail: string[];
}

/* ---------------- Profile ---------------- */

export interface TradingProfile {
  account_size: number;
  risk_pct: number;
  max_position_pct: number;
  max_sector_pct: number;
  max_portfolio_heat_pct: number;
  max_positions: number;
  target_r_multiple: number;
  earnings_gate_days: number;
  time_stop_trading_days: number;
  atr_multiplier: number;
  [key: string]: number;
}

/* ---------------- Ticker analysis ---------------- */

export interface TickerDatesResponse {
  symbol: string;
  dates: string[]; // descending
}

export interface TickerDoc {
  name: string; // report | technical | fundamental | news
  content: string;
}

export interface TickerAnalysisResponse {
  symbol: string;
  date: string;
  docs: TickerDoc[];
  charts: string[]; // timeframes available, e.g. ["daily","weekly"]
}

/** GET /api/analysis/tickers — which tickers already have saved analysis. */
export interface AnalysisIndexEntry {
  latest: string | null;
  count: number;
  dates: string[]; // all analysis dates, ascending (YYYY-MM-DD)
}

export interface AnalysisIndexResponse {
  tickers: Record<string, AnalysisIndexEntry>;
}

/* ---------------- OHLCV bars (live TradingView data layer) ---------------- */

/** One price bar. `time` is a Unix timestamp in seconds (lightweight-charts UTCTimestamp). */
export interface OhlcvBar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/**
 * GET /api/ohlcv/:symbol — live OHLCV pulled from the vendored `tv` CLI (TradingView
 * Desktop via CDP). Like the IB snapshot, `ok` is false (with a human `error`) when
 * TradingView Desktop is down / unreachable, so the chart modal degrades gracefully.
 */
export interface OhlcvResponse {
  ok: boolean;
  symbol: string; // as requested
  resolved: string | null; // symbol TradingView actually resolved (e.g. "NASDAQ:AAPL")
  timeframe: string; // D / W / M / 60 / 240 ...
  bars: OhlcvBar[]; // ascending by time
  error: string | null;
  source: string | null; // "live" | "fixture"
  generated_at: string | null;
}

/* ---------------- Actions / jobs ---------------- */

export type SchedulerSlot = 'premarket' | 'evening-prep' | 'intraday' | 'weekly' | 'monthly';
export type JobStatus = 'running' | 'done' | 'error' | 'busy';

export interface JobLogLine {
  t: number;
  stream: 'stdout' | 'stderr' | 'system';
  line: string;
}

export interface JobSummary {
  id: string;
  label: string;
  status: JobStatus;
  startedAt: number;
  endedAt: number | null;
  exitCode: number | null;
  meta?: Record<string, unknown>;
}

export interface JobDetail extends JobSummary {
  cmd: string;
  args: string[];
  lines: JobLogLine[];
}

export interface StartJobResponse {
  ok: boolean;
  job?: JobSummary;
  busy?: boolean;
  activeJobId?: string;
  error?: string;
}

/** GET /api/trading-plan */
export interface TradingPlanResponse {
  content: string;
}

/** One entry in the Documentation modal sidebar. */
export interface DocSectionMeta {
  id: string; // url-safe slug, e.g. "ftd-detector"
  title: string; // RU title shown in the sidebar
  group: string; // RU group label used to cluster sidebar entries
}

/** GET /api/docs — ordered sections (no content) for the sidebar. */
export interface DocsIndexResponse {
  sections: DocSectionMeta[];
}

/** GET /api/docs/:id — one section's rendered markdown. */
export interface DocSectionResponse extends DocSectionMeta {
  content: string;
}
